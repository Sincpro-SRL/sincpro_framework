"""What a unit of reading touched: every repository read notes its aggregate.

    with noting_reads() as reads:
        answer = bus(QueryInvoiceSummary(...))
    reads            →  {'billing.domain.Invoice', 'billing.domain.Customer'}

Context: what reads the aggregates is the store, not the use case, so the store notes them and
whatever sits above the use case — a query cache — learns what an answer depends on without the
use case declaring anything. A `Repository` of yours notes its reads with `note_read(model)`;
one that does not is one whose answers are never cached. Units nest: an outer one sees every
read an inner one saw. Outside any unit, noting is a no-op.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_units: ContextVar[tuple[set[str], ...]] = ContextVar("sincpro_reading_units", default=())


def aggregate_tag(model: type) -> str:
    """The name an aggregate is noted and invalidated by: its module and class."""
    return f"{model.__module__}.{model.__qualname__}"


def reading() -> bool:
    """Whether some unit of reading is open — so a store skips the work when nobody listens."""
    return bool(_units.get())


def note_read(model: type) -> None:
    for unit in _units.get():
        unit.add(aggregate_tag(model))


@contextmanager
def noting_reads() -> Iterator[set[str]]:
    unit: set[str] = set()
    token = _units.set((*_units.get(), unit))
    try:
        yield unit
    finally:
        _units.reset(token)
