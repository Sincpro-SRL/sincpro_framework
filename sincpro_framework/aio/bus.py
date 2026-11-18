"""AsyncBus: the async door of the bus a project holds — `await billing.get_async_bus()(dto)`.

A bus is synchronous end to end, so a caller that is itself `async def` hands each call to a
thread, and keeps its event loop free while the use case runs. The thread is one of the bus's
async threads (`aio/async_threads.py`) — its own when it declared `async_threads`, the process's
otherwise —, each call in a copy of its caller's context: `self.context` reads what the caller had
set, and nothing the call sets leaks back.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Type

from sincpro_framework.aio.async_threads import on_async_thread

if TYPE_CHECKING:
    from ..sincpro_abstractions import TypeDTO, TypeDTOResponse


class AsyncBus:
    """Async facade over a sync bus.

    Stateless and safe to reuse concurrently (`asyncio.gather`, a `TaskGroup`): every call takes
    its own copy of the caller's context, so there is nothing shared to race on between calls.

        async_bus = billing.get_async_bus()
        async with asyncio.TaskGroup() as group:
            for dto in dtos:
                group.create_task(async_bus(dto))

    As many calls run at once as the bus has async threads; the next ones wait their turn. A
    caller that stops waiting — a `TaskGroup` sibling failed, an `asyncio.timeout` passed — drops
    a call still waiting its turn; one already running runs to its end, its
    `self.context.cancelled` set, since a thread cannot be stopped from outside.
    Design for that: idempotency, and no assumption that a timeout stopped the work.

    Prefer `asyncio.TaskGroup` (3.11+) over `asyncio.gather` to fan out: it cancels its siblings
    on the first failure and raises an `ExceptionGroup`, where `gather` leaves them running.
    """

    def __init__(self, run: Callable[..., Any]) -> None:
        """Wrap whatever runs a DTO synchronously — nothing else is stored, so one instance
        serves any number of concurrent awaits."""
        self._run = run
        """What runs a DTO on an async thread: the `UseFramework` itself — which builds on first
        use, opens the call's scope and forwards a context hosted elsewhere —, and whose async
        threads it runs on."""

    async def execute(
        self, dto: "TypeDTO", return_type: "Type[TypeDTOResponse] | None" = None
    ) -> "TypeDTOResponse | None":
        """Run the DTO on one of the bus's async threads, in a copy of the caller's context.

        await billing.get_async_bus().execute(CommandIssue(total=10), Issued)  ->  Issued
        """
        return await on_async_thread(self._run, self._run, dto, return_type)

    async def __call__(
        self, dto: "TypeDTO", return_type: "Type[TypeDTOResponse] | None" = None
    ) -> "TypeDTOResponse | None":
        """`await async_bus(dto)` — the same as `await async_bus.execute(dto)`, as a sync
        `Bus` is called.

            await billing.get_async_bus()(CreateInvoice(amount=10))   ->  InvoiceCreated(...)
        """
        return await self.execute(dto, return_type)
