"""What the approvals refuse, beside the framework's own errors their code raises."""

from sincpro_framework.exceptions import ClientError, FailureKind, ProgrammingError


class ApprovalNotFound(ClientError):
    """The approval the caller named is not stored — never requested, someone else's, or past the
    store's time."""

    failure_kind = FailureKind.NOT_FOUND


class ApprovalClosed(ClientError):
    """The approval was decided already, and cannot be decided otherwise: one rejected or expired
    is never approved, one approved is never rejected. `state` says which it is."""

    failure_kind = FailureKind.CONFLICT

    def __init__(self, message: str, state: str) -> None:
        """The message for the caller, and the state the approval is in."""
        super().__init__(message)
        self.state = state
