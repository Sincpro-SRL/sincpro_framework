"""What a call to a bounded context hosted elsewhere raises, and how an error crosses back.

    ContextUnavailable     it did not run — nobody answered, or the service there does not host
                           the context; a retry may succeed
    ContextOutcomeUnknown  it may have run — the call was sent and no answer came back
    ContextTimeout         the deadline passed after the call was sent: an outcome unknown
    DTODoesNotFit          a DTO or an answer does not fit the class that rebuilds it
    ContextFailed          the host raised a class this process never imported

Context: everything else the hosting service raises arrives as itself — its class, its `args`
and its attributes (`ContextRequired.missing`, a `DomainError`'s fields), rebuilt here without
calling its `__init__`, so a class whose constructor takes more than a message travels too.
"""

from typing import Any

from pydantic import ValidationError

from sincpro_framework.remote_execution.domain.payload import (
    imported_class,
    pack,
    unpack,
)
from sincpro_framework.transport.failures import FailureKind


class ContextFailed(Exception):
    """The hosting service raised something this process cannot raise as itself — its class is
    not imported here. `kind` names it."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


class ContextUnavailable(Exception):
    """It did not run: nobody answered where the context is hosted, or the service there does
    not host it. A retry may succeed."""

    failure_kind = FailureKind.UNAVAILABLE


class ContextOutcomeUnknown(Exception):
    """It may have run: the call was sent and no answer came back. Verify — or retry with an
    idempotency key — before doing it again or undoing it."""

    failure_kind = FailureKind.UNKNOWN_OUTCOME


class ContextTimeout(ContextOutcomeUnknown):
    """The hosting service did not answer before the call's deadline."""


class DTODoesNotFit(Exception):
    """A DTO or an answer does not fit the class that rebuilds it — a required field missing, a
    value of another type. `fields` says which, each with why."""

    failure_kind = FailureKind.INVALID

    def __init__(self, what: str, error: ValidationError) -> None:
        self.fields = [
            f"{'.'.join(str(one) for one in detail['loc']) or '(root)'}: {detail['msg']}"
            for detail in error.errors()
        ]
        super().__init__(f"{what} does not fit — {'; '.join(self.fields)}")


DETAILS_LIMIT = 4096
"""Bytes an error's details may take — they ride in a header or trailing metadata, which every
wire caps; past it the error travels by its message alone."""


def _writable(value: Any) -> bool:
    try:
        pack(value)
    except Exception:
        return False
    return True


def error_details(error: BaseException) -> bytes:
    """What `error` carries beyond its class — its `args` and its own attributes, packed; an
    attribute that cannot be written is left behind, and nothing travels when the rest does not
    fit in a header.

    Context: dunder attributes (`__notes__`, what the framework marks a failure with) describe
    this process's handling of the error, not the error — they stay here.
    """
    attributes = {
        name: value
        for name, value in getattr(error, "__dict__", {}).items()
        if not name.startswith("__") and _writable(value)
    }
    args = error.args if _writable(error.args) else tuple(str(one) for one in error.args)
    details = pack((args, attributes))
    return details if len(details) <= DETAILS_LIMIT else b""


def _rebuilt(raised: type[Exception], details: bytes) -> Exception | None:
    """`raised` with the `args` and attributes it was sent with, never calling its `__init__` —
    `None` when they did not travel."""
    try:
        args, attributes = unpack(details, None)
        error = raised.__new__(raised)
        error.args = tuple(args)
        if hasattr(error, "__dict__"):
            error.__dict__.update(attributes)
        return error
    except Exception:
        return None


def raised_as_itself(
    module: str | None, kind: str | None, message: str, details: bytes | None = None
) -> Exception:
    """The exception the hosting service raised, as this process raises it.

        raised_as_itself("my_erp.errors", "SignerDown", "hsm-1 failed 3 times", details)
        →  SignerDown("hsm-1", 3)                                with its attributes
        →  ContextFailed("my_erp.errors.SignerDown", …)          the class is not imported here

    1. The class, when this process already imported it — never imported for the remote.
    2. Rebuilt with the `args` and attributes it travelled with.
    3. Else built from the message alone.
    4. Final: that exception, or `ContextFailed` naming the remote class.
    """
    raised: Any = imported_class(module, kind)
    if isinstance(raised, type) and issubclass(raised, Exception):
        rebuilt = _rebuilt(raised, details) if details else None
        if rebuilt is not None:
            return rebuilt
        try:
            return raised(message)
        except Exception:
            pass
    return ContextFailed(
        f"{module}.{kind}" if kind else "an error that named no class", message
    )
