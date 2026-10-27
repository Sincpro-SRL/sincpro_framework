"""What an approval is: declared on a use case, pending in the context's store, decided by its id.

    @sales.feature(CommandCreateQuotation)
    class CreateQuotation(Feature):
        approval = Approval(expires_in=timedelta(minutes=30))

        def preview(self, dto: CommandCreateQuotation) -> str:      read, never written
            return f"Quotation for {dto.customer}, Bs {dto.total}"

        def execute(self, dto: CommandCreateQuotation) -> ResponseQuotation: ...

    sales(CommandCreateQuotation(...))      ->  ApprovalRequired(approval_id="9fK…", preview=…)
    sales(Approve(approval_id="9fK…"))      ->  ResponseQuotation — the input approved, run once

`ApprovalRecord` is what the store holds of one: the exact input, the preview a person read,
whose it is, how it stands and until when. `ApprovalScope` is what an execution says of it, on its
node of the context: approved — the use cases it runs never ask again — or previewing — they only
say what they would do.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import Field

from sincpro_framework.sincpro_abstractions import DataTransferObject

DEFAULT_EXPIRES_IN = timedelta(minutes=15)
"""How long an approval waits for a decision, by default."""

APPROVAL_REQUIRED = "approval_required"
"""What `ApprovalRequired.status` says — the mark a client tells it apart by."""


@dataclass(frozen=True)
class Approval:
    """A use case that runs only once a person approved what it would do — declared as
    `approval = Approval()` on a Feature or an ApplicationService that has a `preview`."""

    expires_in: timedelta = DEFAULT_EXPIRES_IN
    """How long it waits for a decision: past it, it can no longer be approved."""


class Preview(DataTransferObject):
    """What a person reads before approving — the text, and whatever a screen shows beside it."""

    text: str
    data: dict[str, Any] = Field(default_factory=dict)
    """Structured values for a screen of the project's own; nothing reads them here."""


class ApprovalState(StrEnum):
    """Where an approval stands — what `FindApprovals(state=...)` filters by."""

    PENDING = "pending"
    """Waiting for a decision."""
    APPROVED = "approved"
    """Approved: its input ran, or is running."""
    REJECTED = "rejected"
    EXPIRED = "expired"
    """Nobody decided before `expires_at`."""


class ApprovalRecord(DataTransferObject):
    """One approval, as the context's store holds it."""

    approval_id: str
    use_case: str
    """The use case it runs, by the identity of its Command — `sales.CommandCreateQuotation`."""
    input: dict[str, Any]
    """The Command as it was asked, as JSON values: what runs once approved — never another."""
    preview: Preview
    owner: str = ""
    """Whose it is — what the context said when it was asked, `tenant_id=acme,…,user_id=ana`."""
    state: ApprovalState = ApprovalState.PENDING
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    answered_at: datetime | None = None
    """When its approved run answered — an approval answered never runs again, even once its
    answer is no longer replayed."""
    reason: str = ""
    """Why it was rejected, as the person said it."""
    store_version: int = 0
    """What the store says: the version to hand back as `expected`. Opaque: compare it, never
    count with it."""

    def seen_at(self, now: datetime) -> "ApprovalRecord":
        """The approval as it stands at `now`: one still pending past `expires_at` is expired.

        record.seen_at(record.expires_at + timedelta(seconds=1)).state  ->  EXPIRED
        """
        if self.state is ApprovalState.PENDING and now >= self.expires_at:
            return self.model_copy(update={"state": ApprovalState.EXPIRED})
        return self


class ApprovalRequired(DataTransferObject):
    """What a use case that needs approval answers instead of running: what it would do, and the
    id that approves it. An answer, not an error — every wire carries it as a result."""

    status: str = APPROVAL_REQUIRED
    approval_id: str
    """What `Approve` and `Reject` name; empty while only previewing, inside another's preview."""
    use_case: str
    preview: Preview
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ApprovalScope:
    """What an execution says of an approval, on its node of the context — an object, so it
    never travels to another process: there, nothing was approved."""

    approval_id: str
    """The approval being run; empty while previewing."""
    previewing: bool = False
    """Only saying what would be done: the use cases run inside answer their preview and run
    nothing."""
