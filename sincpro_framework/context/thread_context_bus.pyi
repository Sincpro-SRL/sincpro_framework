from contextvars import Context
from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from ..sincpro_abstractions import Bus, TypeDTO, TypeDTOResponse

class ThreadContextBus:
    """
    A `Bus` handle bound to a captured `contextvars` snapshot.

    Returned by `Bus.thread_context()`. `Context.run(...)` replays the snapshot
    for the duration of the call regardless of which thread ends up calling it,
    which is what lets `.execute` see the original context after being handed
    off to a `ThreadPoolExecutor` worker.

    Single-use per captured snapshot: a `contextvars.Context` can only be
    entered by one thread at a time, so don't share one `ThreadContextBus`
    across concurrent workers — call `Bus.thread_context()` again for each task.
    """

    def __init__(self, bus: "Bus", ctx: Context) -> None: ...
    def execute(
        self, dto: "TypeDTO", return_type: "Type[TypeDTOResponse] | None" = None
    ) -> "TypeDTOResponse | None":
        """
        Run `execute` inside the captured context, regardless of which thread calls it.

        Raises:
            RuntimeError: if this same `ThreadContextBus` is entered concurrently
                by more than one thread. Call `Bus.thread_context()` again for
                each task instead of reusing one instance across a batch.
        """
        ...
