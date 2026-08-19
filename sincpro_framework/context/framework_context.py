"""
Context implementation for Sincpro Framework

Default ``context()`` is isolated per task/thread via ContextVar.
``global_scope=True`` publishes keys on the UseFramework instance so concurrent
executions of that same instance can see them.
"""

from contextvars import Token
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Tuple

if TYPE_CHECKING:
    from ..use_bus import UseFramework

_MISSING = object()


class FrameworkContext:
    """
    Framework context manager that provides automatic metadata propagation
    and scope management without mutating injected adapter references.
    """

    def __init__(
        self,
        framework_instance: "UseFramework",
        context: Mapping[str, Any],
        global_scope: bool = False,
    ):
        self._is_entered: bool = False
        self.framework = framework_instance
        self.context: Dict[str, Any] = dict(context)
        self.global_scope: bool = global_scope
        self.parent_context: Dict[str, Any] = framework_instance._get_context().copy()

        self._overlay_token: Optional[Token] = None
        self._overlay: Optional[Dict[str, Any]] = None
        self._shared_snapshot: Optional[Dict[str, Any]] = None
        self._overlay_snapshots: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        self._global_token: Optional[Token] = None

    def __enter__(self) -> "UseFramework":
        """Enter the context manager and return framework instance with context"""
        if self._is_entered:
            raise RuntimeError("Context manager is already entered")

        self._is_entered = True

        if self.global_scope:
            self._enter_global()
        else:
            self._enter_isolated()

        self.framework.logger.debug(f"with context: {self.context}")
        return self.framework

    def _enter_isolated(self) -> None:
        merged = {**self.framework._get_context(), **self.context}
        self._overlay_token, self._overlay = self.framework._push_overlay(merged)

    def _enter_global(self) -> None:
        shared = self.framework._shared_context
        self._shared_snapshot = shared.copy()
        applied = self.context

        self._overlay_snapshots = []
        for overlay in list(self.framework._live_overlays):
            previous = {key: overlay[key] if key in overlay else _MISSING for key in applied}
            self._overlay_snapshots.append((overlay, previous))
            overlay.update(applied)

        shared.update(applied)
        self._global_token = self.framework._in_global_var.set(True)

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the context manager and restore previous context"""
        if self.global_scope:
            self._exit_global()
        elif self._overlay_token is not None and self._overlay is not None:
            self.framework._pop_overlay(self._overlay_token, self._overlay)
        return False

    def _exit_global(self) -> None:
        if self._shared_snapshot is not None:
            self.framework._shared_context.clear()
            self.framework._shared_context.update(self._shared_snapshot)

        for overlay, previous in self._overlay_snapshots:
            for key, value in previous.items():
                if value is _MISSING:
                    overlay.pop(key, None)
                else:
                    overlay[key] = value

        if self._global_token is not None:
            self.framework._in_global_var.reset(self._global_token)
