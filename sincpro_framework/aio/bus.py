"""AsyncBus: async facade over a sync `Bus`, for callers that are themselves async.

`Bus.execute` is sync end to end (see `context/thread_context_bus.py`). A caller
that is already `async def` and wants to fan out several independent DTOs
concurrently (e.g. `asyncio.gather`) still needs a way to hop off the event loop
without losing `self.context` (a `ContextVar` overlay, isolated per OS thread) in
the worker thread `asyncio.to_thread` spawns.

Unlike `ThreadContextBus` (built for a manually-managed `ThreadPoolExecutor`,
which does NOT propagate contextvars on its own), `asyncio.to_thread` already
captures `contextvars.copy_context()` at the call site and runs the target
inside it (stdlib `asyncio/threads.py`) — that's the documented behavior, not
an implementation detail we depend on accidentally. So `AsyncBus` calls
`self._bus.execute` directly: no `ThreadContextBus` involved, no risk of
tripping its single-use-snapshot `RuntimeError` guard on a business exception
that happens to subclass `RuntimeError`.
"""

import asyncio
from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    # Kept out of the runtime import graph on purpose, same reason as
    # context/thread_context_bus.py: sincpro_abstractions imports *this*
    # module (via the aio package) for `Bus.get_async_bus()`, so a real
    # import back here would be circular.
    from ..sincpro_abstractions import Bus, TypeDTO, TypeDTOResponse


class AsyncBus:
    """Async facade over a sync `Bus`.

    Stateless and safe to reuse concurrently (e.g. inside `asyncio.gather`):
    `asyncio.to_thread` captures a fresh context snapshot on every call, so
    there is nothing shared to race on between calls.

    Runs on the calling event loop's default executor (`asyncio.to_thread`,
    stdlib). If the host process also runs an anyio-based server (e.g.
    FastMCP dispatching sync tools via `anyio.to_thread`), that's a second,
    independent thread pool — worth knowing when tuning pool sizes, not a
    correctness concern.

    Cancellation does not stop in-flight work: if the awaiting coroutine is
    cancelled (e.g. `asyncio.wait_for(async_bus(dto), timeout=...)` expiring),
    the `Feature`/`ApplicationService` already running in its worker thread
    keeps running to completion — Python cannot forcibly kill a thread. Design
    for that (idempotency, no assumption that a timeout actually stopped the
    work) rather than relying on cancellation as an abort mechanism.

    For fanning out several calls with proper partial-failure handling,
    prefer `asyncio.TaskGroup` (3.11+) over `asyncio.gather`: a `TaskGroup`
    cancels sibling tasks on the first failure and raises an `ExceptionGroup`,
    instead of `gather`'s default of leaving siblings running and swallowing
    all-but-the-first exception unless `return_exceptions=True` is passed.

    # PYTHON 3.14 FREE-THREADING: `asyncio` itself only gained "first-class
    # support for free-threaded Python" as of 3.14 (per its own docs) — so if
    # this is ever exercised on a free-threaded interpreter, prefer 3.14+ over
    # 3.13t (still the experimental phase for asyncio there). Verified
    # end-to-end on both 3.14.7 and 3.14.7t here (full test suite green on
    # both) — see the note in ioc.py about `dependency_injector` forcing the
    # GIL back on at import time on the free-threaded build, independent of
    # this module.
    """

    def __init__(self, bus: "Bus") -> None:
        self._bus = bus

    async def execute(
        self, dto: "TypeDTO", return_type: "Type[TypeDTOResponse] | None" = None
    ) -> "TypeDTOResponse | None":
        """Run `execute` on a worker thread, preserving the calling context.

        `asyncio.to_thread` propagates the caller's `contextvars.Context` to
        the worker thread on its own — see the module docstring.
        """

        def _call() -> "TypeDTOResponse | None":
            # Same known pyright edge case as ThreadContextBus.execute: `Bus`
            # is only resolvable here via a TYPE_CHECKING import (to avoid a
            # circular import with sincpro_abstractions), which pyright can't
            # fully reconcile with `execute`'s per-call generic signature.
            # Verified correct at runtime — see tests/test_async_bus.py.
            return self._bus.execute(dto, return_type)  # pyright: ignore[reportArgumentType]

        return await asyncio.to_thread(_call)

    async def __call__(
        self, dto: "TypeDTO", return_type: "Type[TypeDTOResponse] | None" = None
    ) -> "TypeDTOResponse | None":
        return await self.execute(dto, return_type)
