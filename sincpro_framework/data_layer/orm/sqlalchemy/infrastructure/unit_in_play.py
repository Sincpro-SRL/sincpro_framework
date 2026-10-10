"""The unit of work in play, per database, on this thread or task — what a repository that was
not handed one joins.

    with self.repository.context():                    opened here, explicitly
        self.feature_bus(CommandPostInvoices(…))       its Feature's self.repository joins it
        self.feature_bus(CommandReconcile(…))          …and so does this one
    →  one commit for both, or one rollback

**Opened explicitly, joined implicitly — and only on the same `Database`.** A unit of work is
always opened by a `context()` somebody wrote; what is implicit is only that a call made inside
it, on the same database, runs in it rather than committing on its own. That is the propagation
Spring calls `REQUIRED` and .NET's `TransactionScope` gives by default. A repository on another
`Database` never joins: two engines share no transaction, and pretending they did is the
two-phase commit this framework does not offer — that is the outbox's and the saga's job.

A `ContextVar`, so a thread or an async task sees only the unit of work it opened; a worker
thread started inside a block does not inherit it, which is what keeps one session from being
used by two threads at once.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy.orm import Session

from sincpro_framework.data_layer.orm.sqlalchemy.domain.transaction import Transaction


@dataclass(frozen=True)
class InPlay:
    """A unit of work being run: its session, and what its transaction was opened with."""

    session: Session
    transaction: Transaction


_in_play: ContextVar[tuple[tuple[object, InPlay], ...]] = ContextVar(
    "sincpro_unit_of_work_in_play", default=()
)
"""Per database, by identity: a `Database` is one engine and one pool, and only the same one
can share a transaction."""


def in_play(database: object) -> InPlay | None:
    """The unit of work open on this database here, or `None`."""
    for one, unit in reversed(_in_play.get()):
        if one is database:
            return unit
    return None


@contextmanager
def playing(database: object, session: Session, transaction: Transaction) -> Generator[None]:
    """Makes this unit of work the one in play on `database` for the block. Ends before the
    commit, so what runs after it — `after_commit` — writes through a unit of work of its own.
    """
    token = _in_play.set((*_in_play.get(), (database, InPlay(session, transaction))))
    try:
        yield
    finally:
        _in_play.reset(token)
