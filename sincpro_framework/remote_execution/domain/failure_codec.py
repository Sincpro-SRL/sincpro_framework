"""How an exception crosses back from a bounded context hosted elsewhere, and is raised here as itself.

    FailureCodec.details(error)                               the hosting service, writing it
    FailureCodec.rebuilt(module, kind, message, details)      the caller, raising it as itself

Context: everything the hosting service raises arrives as itself — its class, its `args` and its
attributes (`ContextRequired.missing`, a `DomainError`'s fields) as JSON values, rebuilt here without
calling its `__init__`, so a class whose constructor takes more than a message travels too. An error
is the one thing still found by `module.qualname`: it is no message, has no identity, and is raised
only when this process already imported its class.
"""

import json
import sys
from typing import Any, get_type_hints

from sincpro_framework.common.serialization import read_values, rebuilt, values_of
from sincpro_framework.remote_execution.domain import exceptions


class FailureCodec:
    """An exception written by the service that raised it, and rebuilt by the one that called."""

    DETAILS_LIMIT = 4096
    """Bytes an error's details may take — they ride in a header or trailing metadata, which every
    wire caps; past it the error travels by its message alone."""

    @classmethod
    def _values(cls, value: Any) -> Any:
        """`value` as JSON values — `None` when it cannot be written, and it stays here."""
        try:
            return values_of(value)
        except Exception:
            return None

    @classmethod
    def details(cls, error: BaseException) -> bytes:
        """What `error` carries beyond its class — its `args` and its own attributes, as JSON; an
        attribute that cannot be written is left behind, and nothing travels when the rest does
        not fit in a header.

        Context: dunder attributes (`__notes__`, what the framework marks a failure with)
        describe this process's handling of the error, not the error — they stay here.

            FailureCodec.details(ContextRequired("CommandPay", ["tenant_id"]))
            ->  b'{"args": [...], "attributes": {"missing": ["tenant_id"], ...}}'
            FailureCodec.details(<args that JSON cannot write>)   ->  args as their `str` each
            FailureCodec.details(<details past 4096 bytes>)       ->  b""

        An empty answer makes the error travel by its message alone.
        """
        attributes = {
            name: written
            for name, value in getattr(error, "__dict__", {}).items()
            if not name.startswith("__") and (written := cls._values(value)) is not None
        }
        args = cls._values(error.args)
        if args is None:
            args = [str(one) for one in error.args]
        details = json.dumps({"args": args, "attributes": attributes}).encode()
        return details if len(details) <= cls.DETAILS_LIMIT else b""

    @classmethod
    def imported_class(cls, module: str | None, qualname: str | None) -> Any:
        """The class `module.qualname` names, when this process already imported it — never an
        import a remote service asked for."""
        found: Any = sys.modules.get(module or "")
        for part in (qualname or "").split("."):
            found = getattr(found, part, None)
        return found if isinstance(found, type) else None

    @classmethod
    def _declared(cls, raised: type[Exception], attributes: dict[str, Any]) -> dict[str, Any]:
        """`attributes` as the class declares them — a `retry_after: timedelta` travels as
        `"PT25S"` and is a `timedelta` again; one it does not declare, or that does not fit, as
        it came. A dunder attribute is this process's mark, never one a remote service sets:
        dropped.
        """
        try:
            hints = get_type_hints(raised)
        except Exception:
            hints = {}
        declared: dict[str, Any] = {}
        for name, value in attributes.items():
            if name.startswith("__"):
                continue
            try:
                declared[name] = rebuilt(value, hints[name]) if name in hints else value
            except Exception:
                declared[name] = value
        return declared

    @classmethod
    def _with_details(cls, raised: type[Exception], details: bytes) -> Exception | None:
        """`raised` with the `args` and attributes it was sent with, never calling its
        `__init__` — `None` when they did not travel."""
        try:
            read = read_values(details)
            error = raised.__new__(raised)
            error.args = tuple(read["args"])
            if hasattr(error, "__dict__"):
                error.__dict__.update(cls._declared(raised, read["attributes"]))
            return error
        except Exception:
            return None

    @classmethod
    def rebuilt(
        cls, module: str | None, kind: str | None, message: str, details: bytes | None = None
    ) -> Exception:
        """The exception the hosting service raised, as this process raises it.

            FailureCodec.rebuilt("my_erp.errors", "SignerDown", "hsm-1 failed 3 times", details)
            →  SignerDown("hsm-1", 3)                                with its attributes
            →  ContextFailed("my_erp.errors.SignerDown", …)          the class is not imported here

        1. The class, when this process already imported it — never imported for the remote.
        2. Rebuilt with the `args` and attributes it travelled with.
        3. Else built from the message alone.
        4. Final: that exception, or `ContextFailed` naming the remote class.
        """
        raised: Any = cls.imported_class(module, kind)
        if isinstance(raised, type) and issubclass(raised, Exception):
            error = cls._with_details(raised, details) if details else None
            if error is not None:
                return error
            try:
                return raised(message)
            except Exception:
                pass
        return exceptions.ContextFailed(
            f"{module}.{kind}" if kind else "an error that named no class", message
        )
