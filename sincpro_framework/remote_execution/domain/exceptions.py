"""What a call to a bounded context hosted elsewhere raises.

    ContextUnavailable     it did not run — nobody answered, the service there does not host
                           the context, or the address's circuit is open; a retry may succeed
    ContextOutcomeUnknown  it may have run — the call was sent and no answer came back
    ContextTimeout         the deadline passed after the call was sent: an outcome unknown
    DTODoesNotFit          a DTO or an answer does not fit the class that rebuilds it
    ContextFailed          the host raised a class this process never imported
    InvalidAddress         an address no wire reaches

Everything else the hosting service raises arrives as itself: how is
`remote_execution.domain.failure_codec`. The first three, raised by this process's own
transport, say `unreached` — what an address's circuit counts; the same classes raised by the
hosting service and rebuilt here are its answer, and do not.
"""

from datetime import timedelta

from pydantic import ValidationError

from sincpro_framework.exceptions import (
    ClientError,
    ExternalServiceError,
    OutcomeUnknownError,
    ProgrammingError,
    ServiceUnavailableError,
    SincproFrameworkNotBuilt,
    UnknownDTOToExecute,
)


class InvalidAddress(ProgrammingError, ValueError):
    """An address no wire can reach — it names what was written and what to write instead."""


class Unreachable(Exception):
    """An error this process's transport raises when the context was not reached or did not
    answer — `unreached` — and the hosting service may raise too, rebuilt here as its answer:
    never `unreached`, whatever it says.

        ContextUnavailable("billing at b:50051 is not answering", unreached=True).unreached  ->  True
        <a ContextUnavailable the hosting service raised, rebuilt here>.unreached          ->  False
    """

    __unreached__: bool = False
    """Set only by this process's transport. A dunder attribute — it never travels with an
    error, so an error rebuilt from another service never carries it."""

    def __init__(self, *args: object, unreached: bool = False) -> None:
        """The error's own `args`; `unreached` only when this process's transport raises it."""
        super().__init__(*args)
        self.__unreached__ = unreached

    @property
    def unreached(self) -> bool:
        """Whether this process's transport raised it because the context was not reached or did
        not answer — what an address's circuit counts; never what the hosting service raised.
        """
        return self.__unreached__ is True


class ContextFailed(ExternalServiceError):
    """The hosting service raised something this process cannot raise as itself — its class is
    not imported here. `kind` names it."""

    def __init__(self, kind: str, message: str) -> None:
        """`kind` and the remote message held apart, for a handler that maps by kind.

        ContextFailed("acme.errors.QuotaExceeded", "no quota left")
            ->  "acme.errors.QuotaExceeded: no quota left"
        """
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


class ContextUnavailable(Unreachable, ServiceUnavailableError):
    """It did not run: nobody answered where the context is hosted, the service there does not
    host it, or the address's circuit is open — calls that went on not reaching it stopped for a
    while. A retry may succeed."""

    retry_after: timedelta | None = None
    """How long to wait before a retry — what is left of an open circuit; a second when unsaid."""


class ContextOutcomeUnknown(Unreachable, OutcomeUnknownError):
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
