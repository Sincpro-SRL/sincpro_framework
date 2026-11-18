from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Type, overload

if TYPE_CHECKING:
    from ..sincpro_abstractions import Bus, TypeDTO, TypeDTOResponse

class AsyncBus:
    """
    The async door of the bus a project holds — `await billing.get_async_bus()(dto)`.

    Each call runs on one of the bus's async threads — its own (`async_threads=`), or the
    process's (`SINCPRO_FRAMEWORK_ASYNC_THREADS`) — in a copy of the caller's context. Stateless and safe to reuse concurrently (`asyncio.TaskGroup`,
    `asyncio.gather`). A caller that stops waiting drops a call still waiting its turn; one
    already running runs to its end, its `self.context.cancelled` set.
    """

    def __init__(self, run: Callable[..., Any]) -> None: ...
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
