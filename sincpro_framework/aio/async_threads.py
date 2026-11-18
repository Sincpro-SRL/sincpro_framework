"""The async threads: where every async door runs the buses. One pool for the process, shared by
every bus, and a pool of its own for a bus that asks for one — a slow context then waits on its
own threads, never on the others'.

    SINCPRO_FRAMEWORK_ASYNC_THREADS=150             the process's pool (40 when unset)
    UseFramework("sandbox", async_threads=8)        sandbox's own eight
    self.context.cancelled                          inside a call: did its caller leave?

Context: a bus is synchronous and an async door — a REST route, a JSON-RPC body, an MCP tool, a
queue's message, a hosted context, `get_async_bus()` — runs on an event loop that serves every
request at once. The door cannot run `execute` on the loop without stopping every other request
while it waits, so it hands the call to a thread and awaits it. A call that waits on I/O holds its
thread the whole wait: the threads are how many calls run at once, and the next ones wait their
turn. A synchronous caller — Odoo, a script, a cron, a Feature calling another bus — runs the bus
in its own thread and never comes here.

A caller that stops waiting — `CancelledError` in the awaiting task — leaves its call one of two
ways: still waiting its turn, it never runs; already running, it runs on, its context's `cancelled`
set, so a use case that works in steps may stop itself.
"""

import asyncio
import threading
import weakref
from collections.abc import Callable
from typing import Any

from sincpro_framework.context.entrypoint.threads import ContextExecutor, call_cancelled
from sincpro_framework.sincpro_conf import settings

_shared: ContextExecutor | None = None
"""The process's pool, started by the first call that needs it — sized then by
`settings.async_threads`, so the settings a project hands over at its start are the ones used."""
_owned: weakref.WeakKeyDictionary[Any, ContextExecutor] = weakref.WeakKeyDictionary()
"""Each pool of a bus that declared `async_threads`, by the bus — let go with it."""
_starting = threading.Lock()


async def on_async_thread[R](
    bus: Any, function: Callable[..., R], *args: Any, **kwargs: Any
) -> R:
    """`function(*args, **kwargs)` on one of `bus`'s async threads, in a copy of the caller's
    context.

        await on_async_thread(route.bus, call_route, route, payload)   ->  (200, {...})
        await on_async_thread(None, dispatch, surface, body)          the process's pool

    1. The pool: `bus`'s own when it declared `async_threads`, started the first time; else the
       process's — for a bus that declared none, and for a call no bus answers (`None`).
    2. The call's flag, set into the copy of the context the thread runs it in — the caller's
       own context is never written.
    3. Awaited. The caller stops waiting (`CancelledError`): a call still waiting its turn is
       dropped, a running one has its flag set; the cancellation raised either way.
    Final: what `function` answered, or what it raised.
    """
    global _shared
    own = getattr(bus, "async_threads", None)
    with _starting:
        if own is not None:
            pool = _owned.get(bus)
            if pool is None:
                pool = _owned[bus] = ContextExecutor(
                    max_workers=own, thread_name_prefix=f"sincpro-async-{bus.name}"
                )
        else:
            if _shared is None:
                _shared = ContextExecutor(
                    max_workers=settings.async_threads, thread_name_prefix="sincpro-async"
                )
            pool = _shared
    flag = threading.Event()

    def flagged() -> R:
        """The call, with its flag in the context it runs in."""
        call_cancelled.set(flag)
        return function(*args, **kwargs)

    try:
        return await asyncio.get_running_loop().run_in_executor(pool, flagged)
    except asyncio.CancelledError:
        flag.set()
        raise
