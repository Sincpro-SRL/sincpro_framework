"""The bounded contexts this execution hosts for a calling service — they run here, whatever the
configuration says.

Context: two services often share one environment, so the one hosting `billing` may read
`billing=grpc://itself` too. The context being hosted for a caller therefore runs here, whatever
the configuration says; marked per context, so a handler here that calls *another* context
hosted elsewhere still reaches it.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

_hosted: ContextVar[frozenset[str]] = ContextVar("sincpro_hosted_here", default=frozenset())


@contextmanager
def hosting(context: str) -> Generator[None]:
    token = _hosted.set(_hosted.get() | {context})
    try:
        yield
    finally:
        _hosted.reset(token)


def hosted_here(context: str) -> bool:
    return context in _hosted.get()


def dto_name(dto_type: type) -> str:
    """The name a bus registers a DTO under — the same in every service running the code."""
    return f"{dto_type.__module__}.{dto_type.__qualname__}"
