"""The context as a bus holds it: `bus.context(...)`, `self.context`, `bus.current_context()`.

    with bus.context(SIATContext(TOKEN="…", SIAT_ENV=SIATEnvironment.TEST)):   a scope, as always
        bus(CommandSendDocument(...))
    with bus.context({"tenant_id": "acme"}, global_scope=True):   what every execution of this bus reads
    with bus.context(restore=["tenant:acme", f"session:{sid}"]):  scopes a store kept
    with bus.context(values, keep_as="sale-77", ttl=timedelta(hours=1)):   kept for whoever resumes

**Entering a bus is entering its node.** The first time a call reaches a bus, the bus's own node
(`Level.BUS` — what it publishes for every execution of its own) goes under the node in play, and the
call's scope under it: an entrypoint when nothing opened the flow, a scope otherwise. A nested call to
the same bus, or one inside its `bus.context(...)`, joins the scope already open.

**What `global_scope=True` writes is the bus's node**: every execution of this bus reads it live —
running ones included — and the block's end puts back what was there.
"""

from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal

from sincpro_framework.context.domain.keys import standardized
from sincpro_framework.context.domain.level import EntrypointKind
from sincpro_framework.context.domain.node import ContextNode
from sincpro_framework.context.domain.store import ContextStore
from sincpro_framework.context.entrypoint.facade import Context
from sincpro_framework.context.infrastructure.said import remember

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework


class FrameworkContext:
    """`bus.context(...)`: a scope of the bus for the block — or, with `global_scope`, what the
    bus publishes."""

    def __init__(
        self,
        framework_instance: "UseFramework",
        context: Mapping[Any, Any] | None = None,
        global_scope: bool = False,
        kind: EntrypointKind | None = None,
        restore: "str | list[str] | None" = None,
        keep_as: str | None = None,
        ttl: timedelta | None = None,
        store: ContextStore | None = None,
    ) -> None:
        self._is_entered: bool = False
        self.framework = framework_instance
        self.context: dict[Any, Any] = standardized(context or {})
        self.global_scope = global_scope
        self.kind = kind
        self.restore = restore
        self.keep_as = keep_as
        self.ttl = ttl
        self.store = store
        self._node: ContextNode | None = None
        self._block: Any = None
        self._published_before: Mapping[Any, Any] | None = None

    def __enter__(self) -> "UseFramework":
        if self._is_entered:
            raise RuntimeError("Context manager is already entered")
        self._is_entered = True
        framework = self.framework
        if self.global_scope:
            self._published_before = framework._published.current
            framework._published.update(framework._coerced(self.context))
        else:
            values = self.context
            if self.restore:
                restored = framework._get_context().restored(self.restore, self.store)
                values = {**restored, **values}
            self._block = framework._scope(framework._coerced(values), self.kind)
            node = self._node = self._block.__enter__()
            if self.keep_as:
                Context(node, framework).keep(self.keep_as, self.ttl, self.store)
        framework.logger.debug(f"with context: {sorted(map(str, self.context))}")
        return framework

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Literal[False]:
        if self.global_scope:
            if self._published_before is not None:
                self.framework._published.replace(self._published_before)
        elif self._block is not None and self._node is not None:
            # What the scope said still counts for the execution it was opened in: its span,
            # its metrics and its error close after the scope does (PRD_03 §4.10).
            remember(self._node.own)
            self._block.__exit__(exc_type, exc_val, exc_tb)
        return False
