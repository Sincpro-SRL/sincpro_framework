from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from types import MappingProxyType
from typing import Any, Dict, List, Optional

from sincpro_framework.bus import FrameworkBus

# The context of the execution in progress, whichever bus runs it — a frozen copy, so what the
# caller changes afterwards never reaches a bus it already handed it to.
_executing: ContextVar[Mapping[str, Any]] = ContextVar(
    "sincpro_executing_context", default=MappingProxyType({})
)


_live: ContextVar["ContextMixin | None"] = ContextVar("sincpro_live_context", default=None)
"""The bus running the execution in progress — read when a signal is recorded, so what a
handler, a hook or an interceptor wrote to its context midway counts, a `bus.context(...)` opened
inside the execution included (PRD_03 §4.10)."""


def live_context() -> Mapping[str, Any]:
    """The context of the execution in progress as it is now — read-only; outside a bus, what a
    caller handed the next one (`carrying`)."""
    bus = _live.get()
    if bus is None:
        return _executing.get()
    return MappingProxyType(bus._get_context())


def hidden_keys() -> frozenset[str]:
    """What the bus running the execution keeps off every signal (`hide_in_logs`)."""
    bus = _live.get()
    return frozenset(getattr(bus, "_hidden_in_logs", ())) if bus is not None else frozenset()


def executing_context() -> Mapping[str, Any]:
    """The context of the execution in progress, whichever bus runs it — read-only, empty
    outside one. What a component beside the buses reads, an auth decision among them."""
    return _executing.get()


@contextmanager
def carrying(context: Mapping[str, Any]) -> Generator[None, None, None]:
    """Every bus executed inside the block starts from `context`.

    Context: what a caller that is not a bus — a cron, a worker — uses to hand its context to
    the buses it calls, the way one bus hands its own to the next.
    """
    token = _executing.set(MappingProxyType({**_executing.get(), **context}))
    try:
        yield
    finally:
        _executing.reset(token)


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

    def current_context(self) -> Mapping[str, Any]:
        """What the context in play says, read-only — empty outside one.

            Database(url, actor=lambda: bus.current_context().get("user.id"))

        `context(...)` is the method that *opens* one and `self.context` is what a Feature
        reads inside a handler. This is the third thing, and the one an adapter needs: a read
        from outside a handler, at the moment it is asked, by something wired long before any
        request existed. `AuditedMixin` stamping `created_by` is exactly that.

        **A live view, not a copy**: it follows the context in play, so a callable stored once
        at wiring time keeps answering correctly for every later request. Read-only because the
        framework owns this dict — writing to it is `context(...)`'s job.
        """
        return MappingProxyType(self._get_context())

    def _inherited_context(self) -> Dict[str, Any]:
        """What the execution in progress — on this bus or the one that called it — carries."""
        return dict(_executing.get())

    @contextmanager
    def _executing_with_context(self) -> Generator[None, None, None]:
        token = _executing.set(MappingProxyType(dict(self._get_context())))
        live = _live.set(self)
        try:
            yield
        finally:
            _live.reset(live)
            _executing.reset(token)

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
