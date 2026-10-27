"""What is asked of the approvals through the bus: deciding one by its id, and reading them.

    sales(Approve(approval_id="9fK…"))                     ->  what the approved use case answers
    sales(Reject(approval_id="9fK…", reason="wrong price"))->  ApprovalRecord(state=REJECTED)
    sales(GetApproval(approval_id="9fK…"))                 ->  ApprovalRecord
    sales(FindApprovals())                                 ->  ResponseApprovals — the pending ones

Registered on a bus by one line, `@sales.feature([Approve, Reject, GetApproval, FindApprovals])`
over an `Approvals`, and published on its wires like any Command.
"""

from sincpro_framework.approvals.domain.approval import ApprovalRecord, ApprovalState
from sincpro_framework.sincpro_abstractions import DataTransferObject


class Approve(DataTransferObject):
    """Run what the approval holds — its input, as it was asked — once."""

    approval_id: str


class Reject(DataTransferObject):
    """Close the approval without running anything."""

    approval_id: str
    reason: str = ""


class GetApproval(DataTransferObject):
    """One approval of the caller's, by its id."""

    approval_id: str


class FindApprovals(DataTransferObject):
    """The caller's approvals, the most recently asked first."""

    state: ApprovalState | None = ApprovalState.PENDING
    """Only those in this state — the pending ones by default; `None`, all."""
    limit: int = 50


class ResponseApprovals(DataTransferObject):
    """What `FindApprovals` answers."""

    approvals: list[ApprovalRecord]
