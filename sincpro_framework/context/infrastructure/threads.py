"""The context handed to another thread — the same on every Python build.

    ContextThread(target=work).start()        a thread that starts where its creator stood
    ContextExecutor(max_workers=8)            a pool that hands each task the submitter's context
    executor.submit(in_context(work), …)      one function, for a pool the project owns

**Why the framework does it, and not the interpreter.** An asyncio task starts from a copy of its
creator's context (PEP 567). A `threading.Thread` starts from none on the default build, and from a
copy on the free-threaded one (`sys.flags.thread_inherit_context`, Python 3.14). A
`ThreadPoolExecutor` hands a worker nothing — the documented fix is
`executor.submit(copy_context().run, fn)`. Code that relied on one build's behaviour would lose the
context on the other; these do the same everywhere.

**One copy per task.** A copied `contextvars.Context` can be entered by one thread at a time, so
every submission takes its own. The node in play is the same in each copy, and nodes are
copy-on-write: what a task sees is never half written, and what it sets stays in its own node.
"""

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import Context, copy_context
from typing import Any


def in_context[R](function: Callable[..., R]) -> Callable[..., R]:
    """`function`, run in a copy of the context it was wrapped in — whichever thread calls it."""
    captured = copy_context()

    def run(*args: Any, **kwargs: Any) -> R:
        return captured.run(function, *args, **kwargs)

    run.__name__ = getattr(function, "__name__", "in_context")
    run.__doc__ = getattr(function, "__doc__", None)
    return run


class ContextThread(threading.Thread):
    """A `threading.Thread` that starts in the context of whoever created it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._captured: Context = copy_context()

    def run(self) -> None:
        self._captured.run(super().run)


class ContextExecutor(ThreadPoolExecutor):
    """A `ThreadPoolExecutor` whose every task runs in a copy of its submitter's context."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        return super().submit(copy_context().run, fn, *args, **kwargs)
