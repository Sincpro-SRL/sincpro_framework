"""`ApprovalGate`: the interceptor an `Approval` brings — a use case that needs approval answers
what it would do, and runs only the input a person approved.

    sales(CommandCreateQuotation(...))   ->  ApprovalRequired(approval_id="9fK…", preview=…)
                                             the input stored, pending; `execute` never ran
    inside `Approve("9fK…")`             ->  the use case runs, as any other

The bus attaches one per use case that declares `approval = Approval()`, innermost: the guards
and interceptors of the project run around the preview as around the run. What it reads of the
execution is the `ApprovalScope` on the context:

- none: the preview, and a pending approval stored — `ApprovalRequired` answered;
- approved: the use case runs — it is the one approved, or one it runs;
- previewing: the preview only, nothing stored — a use case asking for one approval of
  several (`self.feature_bus(...)` inside its own `preview`) collects what each would do.
"""

from typing import Any, get_type_hints

from sincpro_framework.approvals.domain import exceptions
from sincpro_framework.approvals.domain.approval import (
    Approval,
    ApprovalRequired,
    ApprovalScope,
    ApprovalState,
    Preview,
)
from sincpro_framework.approvals.services.requests import ApprovalRequests
from sincpro_framework.common.naming import registered_name
from sincpro_framework.context import use_context
from sincpro_framework.data_layer.caching import JsonCodec
from sincpro_framework.observability import traces


class ApprovalGate:
    """The interceptor of one use case that needs approval — its handler, its Command, what it
    declared and whose its approvals are."""

    def __init__(
        self,
        handler: Any,
        command: type,
        declared: Approval,
        owned_by: tuple[str, ...],
        bus: str,
    ) -> None:
        """The gate of `handler`, answering `command` on the bus named `bus`.

        A handler with no `preview`, or whose `execute` declares no response — what a second
        approval answers again — is refused (`ProgrammingError`): it could never be approved.
        """
        if not callable(getattr(handler, "preview", None)):
            raise exceptions.ProgrammingError(
                f"{type(handler).__name__} declares an approval and has no preview: "
                "def preview(self, dto) -> str, what a person reads before approving"
            )
        self.handler = handler
        self.command = command
        self.declared = declared
        self.owned_by = owned_by
        self.use_case = registered_name(command, bus)
        """The identity of its Command — what an approval names the use case it runs by."""
        response = get_type_hints(type(handler).execute).get("return")
        if response is None:
            raise exceptions.ProgrammingError(
                f"{type(handler).__name__}.execute declares no response type: declare it, so a "
                "second approval answers what the first ran"
            )
        self.codec = JsonCodec(response)
        """What its answer is stored as, so a second approval answers it again."""

    def previewed(self, dto: Any) -> Preview:
        """What `dto` would do, as the handler's `preview` says it — previewing on this
        execution, so a use case it runs only says what it would do too."""
        use_context().set(ApprovalScope, ApprovalScope("", previewing=True))
        said = self.handler.preview(dto)
        return said if isinstance(said, Preview) else Preview(text=str(said))

    def __call__(self, dto: Any, call_next: Any) -> Any:
        """Run an approved use case; answer what any other would do, and store it to approve.

        1. Approved — this one, or one it runs: run it.
        2. Previewing, inside another's preview: what it would do, nothing stored.
        Final: what it would do, stored as a pending approval of its input as it was asked —
        `ApprovalRequired`, with the id that approves it."""
        scope = use_context().get(ApprovalScope)
        if scope is not None and not scope.previewing:
            return call_next(dto)
        preview = self.previewed(dto)
        if scope is not None:
            traces.annotate({"approval.state": "previewed"})
            return ApprovalRequired(approval_id="", use_case=self.use_case, preview=preview)
        record = ApprovalRequests.of(by=self.owned_by).open(
            self.use_case, dto.model_dump(mode="json"), preview, self.declared.expires_in
        )
        traces.annotate({"approval.state": ApprovalState.PENDING.value})
        return ApprovalRequired(
            approval_id=record.approval_id,
            use_case=self.use_case,
            preview=preview,
            expires_at=record.expires_at,
        )
