"""A cache that lets go of an aggregate's reads when a write of it commits.

    frames = invalidate_on_commit(database, QueryCache(), Run, Assessment)
    caching = invalidate_on_commit(database, QueryCaching(store))      # every aggregate written

Context: what is written is noted at each flush and the cache is told at the commit — not at the
flush, where a read in between would hold the rows the commit is about to change, and not on a
rollback, which changed nothing. A cache is per process: writes that another process commits are
not seen here, so an aggregate written elsewhere is not one to hold.
"""

from typing import Protocol

from sqlalchemy.orm import Session

from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database

WRITTEN = "sincpro_written_aggregates"


def _note_written(session: Session) -> None:
    """At each flush: the classes of what is about to be inserted, updated or deleted, added
    to what the transaction has written so far."""
    written: set[type] = session.info.setdefault(WRITTEN, set())
    written.update(type(one) for one in (*session.new, *session.dirty, *session.deleted))


def _forget_written(session: Session) -> None:
    """On a rollback: what was noted is dropped — nothing it named was written."""
    session.info.pop(WRITTEN, None)


class Invalidates(Protocol):
    """What a cache lets go of by aggregate — `QueryCache`, `QueryCaching`, yours."""

    def invalidate(self, target: type | None = None) -> None:
        """Let go of what is held about `target` — of everything, for `None`."""
        ...


def invalidate_on_commit[C: Invalidates](
    database: Database, cache: C, *aggregates: type
) -> C:
    """`cache`, told to `invalidate` each of `aggregates` — a subclass written counts — on every
    commit of `database` that wrote it; every aggregate written, when none is named.

        frames = invalidate_on_commit(database, QueryCache(), Run)   ->  the same QueryCache
        a unit that saves a Run commits      ->  frames.invalidate(Run)
        a unit that saves a Run rolls back   ->  nothing let go
        a unit that saves only a Dataset     ->  nothing let go: Run was the one named

    1. At each flush, the classes of what is inserted, updated or deleted are noted on the
       session.
    2. On a rollback, what was noted is dropped.
    3. At the commit, what was noted is taken:
       3.1 no aggregates named — each written class is invalidated;
       3.2 otherwise each named aggregate a written class is, or descends from.
    Final: `cache` answered as given, so it is wired where it is built.
    """

    def let_go(session: Session) -> None:
        """At the commit: each written class invalidated, or — when aggregates were named —
        each named aggregate a written class is, or descends from.

            written {MoveDraft}, aggregates (Move,)   ->  cache.invalidate(Move)
            written {Run}, no aggregates              ->  cache.invalidate(Run)
        """
        written: set[type] = session.info.pop(WRITTEN, set())
        if not aggregates:
            for one in written:
                cache.invalidate(one)
            return
        for aggregate in aggregates:
            if any(issubclass(one, aggregate) for one in written):
                cache.invalidate(aggregate)

    database.after_flush(_note_written).after_commit(let_go).after_rollback(_forget_written)
    return cache
