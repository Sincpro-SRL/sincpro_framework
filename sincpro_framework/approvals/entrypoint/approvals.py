"""`Approvals`: the approvals of a bus decided and read through the bus — what a person, a screen
or an agent asks: approve one, reject one, read one, list the pending ones.

    @sales.feature([Approve, Reject, GetApproval, FindApprovals])
    class SalesApprovals(Approvals): ...

    sales(Approve(approval_id="9fK…"))       ->  what the approved use case answers
    sales(Approve(approval_id="9fK…"))       ->  the same answer, nothing run again

**Approving runs the input stored, never another.** The approval is taken with the version it
was read at — of two decisions at once, on two replicas, one wins — and its use case runs once:
under `bus.idempotency` keyed by the approval, so a second approval, or one arriving while the
first runs, answers what the first did. A run that fails leaves the approval pending, to approve
again or reject. One rejected or expired is never approved, one approved is never rejected
(`ApprovalClosed`); one of somebody else is not found (`ApprovalNotFound`).

Context: whose approvals are decided is the context's, by `owned_by` — the tenant's and the
user's by default: a person decides what they asked. The bus hands this Feature its gates, and
asks the approvals of every use case of the bus by it.
"""

from collections.abc import Sequence
from datetime import timedelta
from typing import Any, ClassVar

from sincpro_framework.approvals.domain import exceptions
from sincpro_framework.approvals.domain.approval import (
    ApprovalRecord,
    ApprovalScope,
    ApprovalState,
)
from sincpro_framework.approvals.domain.decisions import (
    Approve,
    FindApprovals,
    GetApproval,
    Reject,
    ResponseApprovals,
)
from sincpro_framework.approvals.services.gate import ApprovalGate
from sincpro_framework.approvals.services.requests import ApprovalRequests
from sincpro_framework.context import EntryConflict, Owner, use_context
from sincpro_framework.data_layer.caching import Idempotency, IdempotencyPolicy
from sincpro_framework.observability import traces
from sincpro_framework.sincpro_abstractions import Feature
from sincpro_framework.sincpro_logger import logger


class Approvals(Feature):
    """The Feature that answers `Approve`, `Reject`, `GetApproval` and `FindApprovals` for every
    use case of its bus that needs approval."""

    owned_by: ClassVar[Sequence[str]] = Owner.BY_TENANT_AND_USER
    """The keys of the context an approval belongs to — the one who asked decides; with
    `Owner.BY_TENANT`, anyone of the tenant."""
    replayed_for: ClassVar[timedelta] = timedelta(days=1)
    """How long a second approval answers what the first ran."""
    gates: dict[str, ApprovalGate]
    """The gates of the bus, by the use case they guard — handed by the bus when it is built."""
    decides_on: Any
    """The bus an approved input runs on — handed by the bus when it is built."""

    def requests(self) -> ApprovalRequests:
        """The approvals decided and read: the caller's, in the context's store."""
        return ApprovalRequests.of(by=self.owned_by)

    def get(self, dto: GetApproval) -> ApprovalRecord:
        """The approval as it stands, or `ApprovalNotFound`."""
        found = self.requests().read(dto.approval_id)
        if found is None:
            raise exceptions.ApprovalNotFound(f"approval {dto.approval_id} is not stored")
        return found

    def find(self, dto: FindApprovals) -> ResponseApprovals:
        """The caller's approvals in `dto.state`, the most recently asked first."""
        return ResponseApprovals(approvals=self.requests().find(dto.state, dto.limit))

    def reject(self, dto: Reject) -> ApprovalRecord:
        """The approval closed without running anything — the same, rejected again; one approved
        is `ApprovalClosed`, one expired stays expired.

        A decision taken at the same moment elsewhere is read again, and judged as it stands.
        """
        requests = self.requests()
        while True:
            found = self.get(GetApproval(approval_id=dto.approval_id))
            if found.state is ApprovalState.APPROVED:
                raise exceptions.ApprovalClosed(
                    f"approval {dto.approval_id} was approved: it ran, and cannot be rejected",
                    found.state,
                )
            if found.state is not ApprovalState.PENDING:
                return found
            decided = found.model_copy(
                update={
                    "state": ApprovalState.REJECTED,
                    "decided_at": requests.now(),
                    "reason": dto.reason,
                }
            )
            try:
                saved = requests.save(decided, found.store_version)
            except EntryConflict:
                continue
            traces.annotate({"approval.state": ApprovalState.REJECTED.value})
            return saved

    def _taken(self, requests: ApprovalRequests, approval_id: str) -> ApprovalRecord:
        """The approval taken to run: pending, saved as approved over the version it was read
        at — read again when another decision came first.

        1. Rejected: `ApprovalClosed`. Expired: saved as expired, then `ApprovalClosed`.
        2. Approved and answered, its answer no longer replayed: `ApprovalClosed` — it ran.
        3. Approved and never answered — its run died midway: taken again, said.
        Final: approved, saved."""
        while True:
            found = self.get(GetApproval(approval_id=approval_id))
            if found.state is ApprovalState.APPROVED and found.answered_at is not None:
                raise exceptions.ApprovalClosed(
                    f"approval {approval_id} ran at {found.answered_at.isoformat()}: its answer "
                    "is no longer kept, and it never runs again",
                    found.state,
                )
            if found.state is ApprovalState.APPROVED:
                logger.warning(
                    f"approval {approval_id} was approved and its run left no answer: it runs "
                    "again"
                )
                return found
            if found.state is ApprovalState.EXPIRED:
                try:
                    requests.save(found, found.store_version)
                except EntryConflict:
                    pass
                traces.annotate({"approval.state": ApprovalState.EXPIRED.value})
            if found.state is not ApprovalState.PENDING:
                said = f" ({found.reason})" if found.reason else ""
                raise exceptions.ApprovalClosed(
                    f"approval {approval_id} is {found.state.value}{said}: it cannot be approved",
                    found.state,
                )
            taken = found.model_copy(
                update={"state": ApprovalState.APPROVED, "decided_at": requests.now()}
            )
            try:
                return requests.save(taken, found.store_version)
            except EntryConflict:
                continue

    def _ran(self, requests: ApprovalRequests, approval_id: str, gate: ApprovalGate) -> Any:
        """The input the approval holds, run on the bus as approved — marked answered when it
        answers; pending again when the run fails, so it may be approved again or rejected."""
        taken = self._taken(requests, approval_id)
        use_context().set(ApprovalScope, ApprovalScope(approval_id))
        try:
            answer = self.decides_on(gate.command.model_validate(taken.input))
        except Exception:
            pending = taken.model_copy(
                update={"state": ApprovalState.PENDING, "decided_at": None}
            )
            try:
                requests.save(pending, taken.store_version)
            except Exception as error:
                logger.warning(f"approval {approval_id} could not be left pending: {error}")
            raise
        answered = taken.model_copy(update={"answered_at": requests.now()})
        try:
            requests.save(answered, taken.store_version)
        except Exception as error:
            logger.warning(
                f"approval {approval_id} ran, and was not marked answered: {error}"
            )
        return answer

    def approve(self, dto: Approve) -> Any:
        """What the approved use case answers — run once for the approval, answered again to
        every approval after it.

        1. The approval, and the gate of the use case it runs, else `ApprovalNotFound`.
        2. Run under `Idempotency`, keyed by its owner and id: one run across replicas, the
           others waiting for its answer.
        Final: the answer — its span says approved, or replayed."""
        requests = self.requests()
        found = self.get(GetApproval(approval_id=dto.approval_id))
        gate = self.gates.get(found.use_case)
        if gate is None:
            raise exceptions.ProgrammingError(
                f"approval {dto.approval_id} runs {found.use_case}, which no use case of this "
                "bus that declares an approval answers"
            )
        ran: list[bool] = []

        def run() -> Any:
            ran.append(True)
            return self._ran(requests, dto.approval_id, gate)

        policy = IdempotencyPolicy(
            expires_after=self.replayed_for,
            in_progress_for=timedelta(minutes=5),
            wait_for_completion=timedelta(seconds=30),
        )
        answer = Idempotency(namespace="approvals").run(
            ("approval", requests.owner, dto.approval_id), run, policy, gate.codec
        )
        state = ApprovalState.APPROVED.value if ran else "replayed"
        traces.annotate({"approval.state": state})
        return answer

    def execute(self, dto: Any) -> Any:
        """`approve`, `reject`, `get` or `find`, by the DTO — any other is a registration
        mistake: `ProgrammingError`."""
        match dto:
            case Approve():
                return self.approve(dto)
            case Reject():
                return self.reject(dto)
            case GetApproval():
                return self.get(dto)
            case FindApprovals():
                return self.find(dto)
        raise exceptions.ProgrammingError(
            f"{type(self).__name__} answers Approve, Reject, GetApproval and FindApprovals, "
            f"not {type(dto).__name__}"
        )
