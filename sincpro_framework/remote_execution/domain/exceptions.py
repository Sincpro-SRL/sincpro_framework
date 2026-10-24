"""What a call to a bounded context hosted elsewhere raises.

    ContextUnavailable     it did not run — nobody answered, the service there does not host
                           the context, or the address's circuit is open; a retry may succeed
    ContextOutcomeUnknown  it may have run — the call was sent and no answer came back
    ContextTimeout         the deadline passed after the call was sent: an outcome unknown
    DTODoesNotFit          a DTO or an answer does not fit the class that rebuilds it
    ContextFailed          the host raised a class this process never imported

Everything else the hosting service raises arrives as itself: how is `remote_execution.domain.failures`.
The first three, raised by this process's own transport, are marked `unreached` — what an
address's circuit counts; the same classes raised by the hosting service and rebuilt here are its
answer, and are not.
"""

from datetime import timedelta
from typing import TypeVar

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

UNREACHED = "__sincpro_unreached__"
"""What this process's transport leaves on a failure it raised: the context was not reached, or
no answer came back. A dunder attribute — it never travels with an error, so an error rebuilt
from another service never carries it."""

E = TypeVar("E", bound=BaseException)


def unreached(error: E) -> E:
    """`error`, marked as raised by this process's transport — see `UNREACHED`."""
    setattr(error, UNREACHED, True)
    return error


def is_unreached(error: BaseException) -> bool:
    """Whether this process's transport raised `error` because the context was not reached or
    did not answer — never what the hosting service raised, rebuilt here."""
    return getattr(error, UNREACHED, False) is True


class ContextFailed(ExternalServiceError):
    """The hosting service raised something this process cannot raise as itself — its class is
    not imported here. `kind` names it."""

    def __init__(self, kind: str, message: str) -> None:
        """`kind` and the remote message kept apart, for a handler that maps by kind.

        ContextFailed("acme.errors.QuotaExceeded", "no quota left")
            ->  "acme.errors.QuotaExceeded: no quota left"
        """
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


class ContextUnavailable(ServiceUnavailableError):
    """It did not run: nobody answered where the context is hosted, the service there does not
    host it, or the address's circuit is open — calls that kept not reaching it stopped for a
    while. A retry may succeed."""

    retry_after: timedelta | None = None
    """How long to wait before a retry — what is left of an open circuit; a second when unsaid."""


class ContextOutcomeUnknown(OutcomeUnknownError):
    """It may have run: the call was sent and no answer came back. Verify — or retry with an
    idempotency key — before doing it again or undoing it."""


class ContextTimeout(ContextOutcomeUnknown):
    """The hosting service did not answer before the call's deadline."""


class DTODoesNotFit(ClientError):
    """A DTO or an answer does not fit the class that rebuilds it — a required field missing, a
    value of another type. `fields` says which, each with why."""

    def __init__(self, what: str, error: ValidationError) -> None:
        """Each field the validation refused, as `path: why` — `(root)` for the whole value.

        DTODoesNotFit("CommandPay", error).fields  ->  ["amount: Field required"]
        """
        self.fields = [
            f"{'.'.join(str(one) for one in detail['loc']) or '(root)'}: {detail['msg']}"
            for detail in error.errors()
        ]
        super().__init__(f"{what} does not fit — {'; '.join(self.fields)}")
