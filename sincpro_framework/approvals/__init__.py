"""Approvals: a use case that runs only once a person approved what it would do — and then only
the input they approved, once.

    from sincpro_framework.approvals import Approval, Approvals, Approve, FindApprovals, ...

    @sales.feature(CommandCreateQuotation)
    class CreateQuotation(Feature):
        approval = Approval()                          15 minutes to decide, by default

        def preview(self, dto) -> str:                 what a person reads — no effects
            return f"Quotation for {dto.customer}, Bs {dto.total}"

        def execute(self, dto) -> ResponseQuotation: ...

    @sales.feature([Approve, Reject, GetApproval, FindApprovals])
    class SalesApprovals(Approvals): ...

    sales(CommandCreateQuotation(...))   ->  ApprovalRequired(approval_id, preview, expires_at)
    sales(Approve(approval_id=...))      ->  ResponseQuotation — run once; again, the same answer

The pending approval is stored where the context is stored, beside the drafts and the pipeline
runs: the exact input, the preview, whose it is and until when. Approving runs that input, never
one sent again; an ApplicationService that declares an approval asks one for everything it runs,
and the use cases it runs never ask again.

    domain/        Approval, Preview, ApprovalRecord, ApprovalState, ApprovalRequired,
                   ApprovalScope; Approve, Reject, GetApproval, FindApprovals,
                   ResponseApprovals; ApprovalNotFound, ApprovalClosed
    services/      ApprovalRequests — the approvals of whoever the context says, in its store;
                   ApprovalGate — the interceptor that answers the preview instead of running
    entrypoint/    Approvals — the Feature that decides and reads them; BusApprovalsMixin —
                   what a UseFramework adds to attach the gates
"""

from sincpro_framework.approvals.domain.approval import (
    Approval,
    ApprovalRecord,
    ApprovalRequired,
    ApprovalState,
    Preview,
)
from sincpro_framework.approvals.domain.decisions import (
    Approve,
    FindApprovals,
    GetApproval,
    Reject,
    ResponseApprovals,
)
from sincpro_framework.approvals.domain.exceptions import ApprovalClosed, ApprovalNotFound
from sincpro_framework.approvals.entrypoint.approvals import Approvals
from sincpro_framework.approvals.services.requests import ApprovalRequests

__all__ = [
    "Approval",
    "ApprovalClosed",
    "ApprovalNotFound",
    "ApprovalRecord",
    "ApprovalRequests",
    "ApprovalRequired",
    "ApprovalState",
    "Approvals",
    "Approve",
    "FindApprovals",
    "GetApproval",
    "Preview",
    "Reject",
    "ResponseApprovals",
]
