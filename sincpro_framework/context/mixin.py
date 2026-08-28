from contextvars import ContextVar, Token
from typing import Any, Dict, List, Optional

from sincpro_framework.bus import FrameworkBus


class ContextMixin:
    """Per-instance context storage: shared dict + task overlay via ContextVar.

    Kickoff dependencies stay as injected references. This mixin only owns the
    context dict that Features read as ``self.context``.

    # PYTHON 3.14 FREE-THREADING: `_live_overlays` is a plain list, appended to
    # and removed from per execution (`_push_overlay`/`_pop_overlay`) with no
    # lock. The one place it's iterated (`framework_context.py`'s
    # `global_scope` propagation) already takes `list(self._live_overlays)`
    # before looping, which per CPython's own thread-safety guarantees
    # (https://docs.python.org/3/library/threadsafety.html) keeps individual
    # `list.append`/`.remove`/copy operations atomic even under a
    # free-threaded build — described as current-implementation behavior, not
    # a permanent guarantee. No change needed today; flagging so a future
    # reader doesn't have to re-derive it if this ever gets exercised
    # concurrently under free-threading.
    """

    bus: FrameworkBus

    def _init_context_storage(self) -> None:
        self._overlay_var: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
            f"sincpro_ctx_overlay_{id(self)}", default=None
        )
        self._in_global_var: ContextVar[bool] = ContextVar(
            f"sincpro_ctx_global_{id(self)}", default=False
        )
        self._shared_context: Dict[str, Any] = {}
        self._live_overlays: List[Dict[str, Any]] = []

    def _get_context(self) -> Dict[str, Any]:
        overlay = self._overlay_var.get()
        if overlay is not None:
            return overlay
        return self._shared_context

    def _set_context(self, context: Dict[str, Any]) -> None:
        target = self._get_context()
        target.clear()
        target.update(context)

    def _clean_context(self) -> None:
        self._set_context({})

    def _push_overlay(self, data: Dict[str, Any]) -> tuple[Token, Dict[str, Any]]:
        overlay = dict(data)
        token = self._overlay_var.set(overlay)
        self._live_overlays.append(overlay)
        return token, overlay

    def _pop_overlay(self, token: Token, overlay: Dict[str, Any]) -> None:
        try:
            self._live_overlays.remove(overlay)
        except ValueError:
            pass
        self._overlay_var.reset(token)

    def _bind_context_to_handlers(self) -> None:
        if self.bus is None:
            return
        for feature in self.bus.feature_bus.feature_registry.values():
            feature.bind_to_framework(self)
        for app_service in self.bus.app_service_bus.app_service_registry.values():
            app_service.bind_to_framework(self)
