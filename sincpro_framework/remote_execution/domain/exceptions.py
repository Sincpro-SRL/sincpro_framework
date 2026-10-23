"""What a call to a bounded context hosted elsewhere raises.

    ContextUnavailable     it did not run — nobody answered, or the service there does not host
                           the context; a retry may succeed
    ContextOutcomeUnknown  it may have run — the call was sent and no answer came back
    ContextTimeout         the deadline passed after the call was sent: an outcome unknown
    DTODoesNotFit          a DTO or an answer does not fit the class that rebuilds it
    ContextFailed          the host raised a class this process never imported

Everything else the hosting service raises arrives as itself: how is `remote_execution.domain.failures`.
"""

from pydantic import ValidationError

from sincpro_framework.common.transport.exceptions import InvalidAddress
from sincpro_framework.exceptions import (
    ClientError,
    ExternalServiceError,
    OutcomeUnknownError,
    ServiceUnavailableError,
    SincproFrameworkNotBuilt,
    UnknownDTOToExecute,
)


class ContextFailed(ExternalServiceError):
    """The hosting service raised something this process cannot raise as itself — its class is
    not imported here. `kind` names it."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


class ContextUnavailable(ServiceUnavailableError):
    """It did not run: nobody answered where the context is hosted, or the service there does
    not host it. A retry may succeed."""


class ContextOutcomeUnknown(OutcomeUnknownError):
    """It may have run: the call was sent and no answer came back. Verify — or retry with an
    idempotency key — before doing it again or undoing it."""


class ContextTimeout(ContextOutcomeUnknown):
    """The hosting service did not answer before the call's deadline."""


class DTODoesNotFit(ClientError):
    """A DTO or an answer does not fit the class that rebuilds it — a required field missing, a
    value of another type. `fields` says which, each with why."""

    def __init__(self, what: str, error: ValidationError) -> None:
        self.fields = [
            f"{'.'.join(str(one) for one in detail['loc']) or '(root)'}: {detail['msg']}"
            for detail in error.errors()
        ]
        super().__init__(f"{what} does not fit — {'; '.join(self.fields)}")
