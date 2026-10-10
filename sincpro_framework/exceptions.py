"""What can go wrong, by who caused it — and `FailureKind`, what every wire answers it with.

    FrameworkError
    ├── ProgrammingError          the developer: the code or its configuration   → fix the code
    ├── ClientError               the caller: what it sent is wrong              → fix the request
    ├── DomainError (ddd)         a rule of the business says no                 → show the answer
    └── ExternalServiceError      something this call depends on failed
        ├── ServiceUnavailableError   it did not answer                          → retry later
        └── OutcomeUnknownError       it may have run                            → verify, then retry

Anything that is not a `FrameworkError` is a crash nobody foresaw — a bug: it is `INTERNAL`,
logged with its traceback and reported, and nothing of it is told to the caller.

Each base declares its `failure_kind`; a subclass declares another when it means another thing.
A project's own exception does the same, on one of these bases or on its own:

    class InvoiceNotFound(ClientError):
        failure_kind = FailureKind.NOT_FOUND
"""

from enum import StrEnum


class FailureKind(StrEnum):
    """What a failure is, whatever the wire — each wire answers it with its own code, so a
    client of any of them tells the same things apart."""

    INVALID = "invalid"
    """The request did not validate as the DTO."""
    UNAUTHENTICATED = "unauthenticated"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    """What the request names does not exist — declared by the error (`failure_kind = ...`)."""
    CONFLICT = "conflict"
    """A write collided: a newer version of the aggregate, or a duplicate of a unique value."""
    IN_PROGRESS = "in_progress"
    """The same idempotency key is running elsewhere right now — retry once it completes."""
    KEY_REUSED = "key_reused"
    """An idempotency key came back with another payload — a new request needs a new key."""
    DOMAIN = "domain"
    """The domain refused the request — the answer to it, told to the caller."""
    EXHAUSTED = "exhausted"
    """A quota or a rate was spent — retry later. Declared by the error."""
    UNAVAILABLE = "unavailable"
    """Something this call needs is down for now — retry. Declared by the error."""
    UNKNOWN_OUTCOME = "unknown_outcome"
    """The call was sent and no answer came back: it may have run. Verify — or retry with an
    idempotency key — before doing it again or undoing it. Declared by the error."""
    INTERNAL = "internal"
    """The inside of the process failed — nothing of it is told."""


class FrameworkError(Exception):
    """Everything the framework raises on purpose. Catch it to catch all of it — what is left is
    a crash."""


class ProgrammingError(FrameworkError):
    """The developer's: the code or its configuration uses the framework against its contract —
    a DTO registered twice, an extension that cannot work, a write where none is allowed.

    Fix the code; running it again fails the same way. Most are raised while the bus is built.
    """

    failure_kind = FailureKind.INTERNAL


class ClientError(FrameworkError):
    """The caller's: what it sent is wrong — a request that does not validate, a criteria that
    cannot be answered, a record that is not there, missing credentials.

    The caller fixes the request; sending it again as it is fails the same way. Its message is
    written for the caller and told to it.
    """

    failure_kind = FailureKind.INVALID


class ExternalServiceError(FrameworkError):
    """Something this call depends on failed — a database, a broker, a store, another service.

    Neither the code nor the caller is at fault. A subclass says what to do about it; this one
    alone is the other side refusing, and running it again as it is will not help.
    """

    failure_kind = FailureKind.INTERNAL


class ServiceUnavailableError(ExternalServiceError):
    """The dependency did not answer — down, unreachable, past a bound — so the call did not run.

    Retry later; a caller that can go on without it (an offline mode) may.
    """

    failure_kind = FailureKind.UNAVAILABLE


class OutcomeUnknownError(ExternalServiceError):
    """The call was sent and no answer came back: it may have run.

    Verify — or retry with an idempotency key — before doing it again or undoing it.
    """

    failure_kind = FailureKind.UNKNOWN_OUTCOME


class DTOAlreadyRegistered(ProgrammingError):
    """A DTO this layer already answers, or one whose name or identity another DTO holds."""


class DependencyAlreadyRegistered(ProgrammingError):
    """A dependency added under a name the bus already holds."""


class DependencyNotRegistered(ProgrammingError, AttributeError):
    """A dependency read that was never registered."""


class UnknownDTOToExecute(ProgrammingError):
    """A DTO no Feature or ApplicationService of the bus answers."""


class SincproFrameworkNotBuilt(ProgrammingError):
    """The bus could not be built, or was asked for what only a built bus has."""


class BusAlreadyBuilt(ProgrammingError):
    """Something was registered on a bus that is already built, where it would never run —
    register it before the first execution."""


class ContextRequired(ProgrammingError):
    """A use case declared context keys it cannot run without (`requires_context`), and the context
    of this execution does not have them. A use case that declared nothing is never checked.
    """

    def __init__(self, use_case: str, missing: list[str]) -> None:
        super().__init__(
            f"{use_case} requires {', '.join(missing)} in the context — open it with "
            f"bus.context({{...}}), or register a context_provider that gives them"
        )
        self.use_case = use_case
        self.missing = missing
