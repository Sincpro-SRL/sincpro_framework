"""How an exception crosses back from a bounded context hosted elsewhere, and is raised here as itself.

Context: everything the hosting service raises arrives as itself — its class, its `args` and its
attributes (`ContextRequired.missing`, a `DomainError`'s fields) as JSON values, rebuilt here without
calling its `__init__`, so a class whose constructor takes more than a message travels too. An error
is the one thing still found by `module.qualname`: it is no message, has no identity, and is raised
only when this process already imported its class.
"""

import json
import sys
from typing import Any

from sincpro_framework.common.serialization import read_values, values_of
from sincpro_framework.remote_execution.domain import exceptions

DETAILS_LIMIT = 4096
"""Bytes an error's details may take — they ride in a header or trailing metadata, which every
wire caps; past it the error travels by its message alone."""


def _values(value: Any) -> Any:
    """`value` as JSON values — `None` when it cannot be written, and it stays here."""
    try:
        return values_of(value)
    except Exception:
        return None


def error_details(error: BaseException) -> bytes:
    """What `error` carries beyond its class — its `args` and its own attributes, as JSON; an
    attribute that cannot be written is left behind, and nothing travels when the rest does not
    fit in a header.

    Context: dunder attributes (`__notes__`, what the framework marks a failure with) describe
    this process's handling of the error, not the error — they stay here.
    """
    attributes = {
        name: written
        for name, value in getattr(error, "__dict__", {}).items()
        if not name.startswith("__") and (written := _values(value)) is not None
    }
    args = _values(error.args)
    if args is None:
        args = [str(one) for one in error.args]
    details = json.dumps({"args": args, "attributes": attributes}).encode()
    return details if len(details) <= DETAILS_LIMIT else b""


def imported_class(module: str | None, qualname: str | None) -> Any:
    """The class `module.qualname` names, when this process already imported it — never an
    import a remote service asked for."""
    found: Any = sys.modules.get(module or "")
    for part in (qualname or "").split("."):
        found = getattr(found, part, None)
    return found if isinstance(found, type) else None


def _rebuilt(raised: type[Exception], details: bytes) -> Exception | None:
    """`raised` with the `args` and attributes it was sent with, never calling its `__init__` —
    `None` when they did not travel."""
    try:
        read = read_values(details)
        error = raised.__new__(raised)
        error.args = tuple(read["args"])
        if hasattr(error, "__dict__"):
            error.__dict__.update(read["attributes"])
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
    return exceptions.ContextFailed(
        f"{module}.{kind}" if kind else "an error that named no class", message
    )
