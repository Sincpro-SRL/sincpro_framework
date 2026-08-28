"""ThreadContextBus: propagate a captured context across a thread boundary.

`context()` overlays live in a `ContextVar` (see `mixin.py`), which is isolated
per OS thread by design. A Feature/ApplicationService that fans work out to a
`ThreadPoolExecutor` loses `self.context` in every worker unless the context is
explicitly propagated — this module is that propagation mechanism.
"""

from contextvars import Context
from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    # Kept out of the runtime import graph on purpose: sincpro_abstractions
    # imports *this* module for `Bus.thread_context()`, so a real import back
    # here would be circular. Every use below is a quoted forward reference.
    from ..sincpro_abstractions import Bus, TypeDTO, TypeDTOResponse


class ThreadContextBus:
    """A `Bus` handle bound to a captured `contextvars` snapshot.

    Returned by `Bus.thread_context()`. `Context.run(...)` replays the snapshot
    for the duration of the call regardless of which thread ends up calling it,
    which is what lets `.execute` see the original context after being handed
    off to a `ThreadPoolExecutor` worker.

    Single-use per captured snapshot: a `contextvars.Context` can only be
    entered by one thread at a time, so don't share one `ThreadContextBus`
    across concurrent workers — call `Bus.thread_context()` again for each task.
    """

    def __init__(self, bus: "Bus", ctx: Context) -> None:
        self._bus = bus
        self._ctx = ctx

    def execute(
        self, dto: "TypeDTO", return_type: "Type[TypeDTOResponse] | None" = None
    ) -> "TypeDTOResponse | None":
        def _call() -> "TypeDTOResponse | None":
            # `Bus` is only known here via a TYPE_CHECKING import (to avoid a
            # circular import with sincpro_abstractions), which pyright can't
            # fully reconcile with `execute`'s per-call generic signature.
            # Verified correct at runtime — see tests/test_thread_context_bus.py.
            return self._bus.execute(dto, return_type)  # pyright: ignore[reportArgumentType]

        try:
            return self._ctx.run(_call)
        except RuntimeError as error:
            raise RuntimeError(
                "ThreadContextBus.execute() was entered concurrently by more than "
                "one thread using the same captured context. Call "
                "`bus.thread_context()` again for each task you submit (inside the "
                "loop, right before `executor.submit`) instead of reusing one "
                "instance across the whole batch — each submission needs its own "
                "snapshot."
            ) from error
