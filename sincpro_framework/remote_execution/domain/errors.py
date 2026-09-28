"""What a call to a bounded context hosted elsewhere raises when it does not answer as itself."""

import sys
from typing import Any


class ContextFailed(Exception):
    """The hosting service raised something this process cannot raise as itself — its class is
    not imported here, or needs more than a message to be built. `kind` names it."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


class ContextUnavailable(Exception):
    """Nobody answered where the context is hosted, or the service there does not host it."""


class ContextTimeout(Exception):
    """The hosting service did not answer before the call's deadline."""


def _imported(module: str | None, kind: str | None) -> Any:
    """The class `module.kind` names, when this process already imported it — never an import
    a remote service asked for."""
    found: Any = sys.modules.get(module or "")
    for part in (kind or "").split("."):
        found = getattr(found, part, None)
    return found if isinstance(found, type) else None


def raised_as_itself(module: str | None, kind: str | None, message: str) -> Exception:
    """The exception the hosting service raised, as this process raises it.

        raised_as_itself("my_erp.errors", "InvalidRequest", "Content is empty")
        →  InvalidRequest("Content is empty")                    imported here, built from a message
        →  ContextFailed("my_erp.errors.InvalidRequest", …)       otherwise

    1. The class, when this process already imported it — never imported for the remote.
    2. Built from the message alone; one that needs more (a database error) cannot be.
    3. Final: that exception, or `ContextFailed` naming the remote class.
    """
    raised = _imported(module, kind)
    if isinstance(raised, type) and issubclass(raised, Exception):
        try:
            return raised(message)
        except Exception:
            pass
    return ContextFailed(
        f"{module}.{kind}" if kind else "an error that named no class", message
    )
