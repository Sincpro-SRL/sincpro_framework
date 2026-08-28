from typing import TYPE_CHECKING, Type, overload

if TYPE_CHECKING:
    from ..sincpro_abstractions import Bus, TypeDTO, TypeDTOResponse

class AsyncBus:
    """
    Async facade over a sync `Bus`, for callers that are themselves async.

    Dispatches via `asyncio.to_thread`, which propagates the caller's
    `contextvars.Context` to the worker thread on its own (stdlib, documented
    behavior) — no `ThreadContextBus` involved. Stateless and safe to reuse
    concurrently (e.g. inside `asyncio.gather`): each call gets its own fresh
    context snapshot from `asyncio.to_thread` itself.
    """

    def __init__(self, bus: "Bus") -> None: ...
    @overload
    async def execute(
        self, dto: "TypeDTO", return_type: "Type[TypeDTOResponse]"
    ) -> "TypeDTOResponse":
        """Run `execute` on a worker thread with a specified return type, preserving the calling context."""
        ...

    @overload
    async def execute(self, dto: "TypeDTO") -> "TypeDTOResponse | None":
        """Run `execute` on a worker thread, preserving the calling context."""
        ...

    @overload
    async def __call__(
        self, dto: "TypeDTO", return_type: "Type[TypeDTOResponse]"
    ) -> "TypeDTOResponse":
        """Sugar over `execute` with a specified return type."""
        ...

    @overload
    async def __call__(self, dto: "TypeDTO") -> "TypeDTOResponse | None":
        """Sugar over `execute`."""
        ...
