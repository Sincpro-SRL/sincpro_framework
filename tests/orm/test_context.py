"""The unit of work as a long process uses it: commit by batch, undo one group, lock a row,
and reach for SQLAlchemy whole when the criteria cannot say it.

These are the operations a reconciliation needs and a listing never does. What is pinned is
that each one is available only where it means something — a lock outside a unit of work
protects nothing, a session outside one does not exist — and that the provider decides the
engine.
"""

import pytest
from sqlalchemy import func, select

from sincpro_framework.ddd.criteria import Condition, Criteria, Level, Operator
from sincpro_framework.ddd.criteria.pagination import Pagination
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.sql_translator import (
    GRAIN_TRANSLATORS,
    grouping_column,
    register_grain_translator,
)

from .models import ROW_COUNT, Note, Notes, Thing, Things


def titled(store, title: str) -> Notes:
    return store.search(Notes, Criteria(where=Condition(field="title", value=title)))


def test_the_session_exists_only_inside_a_unit_of_work(store):
    with pytest.raises(ContractViolation, match="only inside context"):
        store.session

    with store.context() as repository:
        total = repository.session.execute(select(func.count()).select_from(Thing)).scalar()

    assert total == ROW_COUNT


def test_the_session_is_the_escape_hatch_for_what_the_criteria_cannot_say(store):
    """A join, a window, an aggregate over a projection: SQLAlchemy whole, same transaction."""
    with store.context() as repository:
        # Pyright sees the dataclass field; at runtime it is the instrumented column.
        size = Thing.size  # type: ignore[assignment]
        rows = repository.session.execute(
            select(size, func.count()).group_by(size).order_by(size)  # type: ignore[arg-type]
        ).all()

    assert [count for _, count in rows] == [5, 5, 5, 5, 5]


def test_a_commit_in_the_middle_makes_the_first_half_durable(store):
    with pytest.raises(RuntimeError):
        with store.context() as repository:
            repository.save(Note(title="durable"))
            repository.commit()
            repository.save(Note(title="lost"))
            raise RuntimeError("after the checkpoint")

    assert len(titled(store, "durable")) == 1
    assert len(titled(store, "lost")) == 0


def test_a_savepoint_undoes_one_group_and_the_unit_of_work_goes_on(store):
    with store.context() as repository:
        repository.save(Note(title="kept"))
        try:
            with repository.savepoint():
                repository.save(Note(title="undone"))
                raise ValueError("this group does not reconcile")
        except ValueError:
            pass
        repository.save(Note(title="kept"))

    assert len(titled(store, "kept")) == 2
    assert len(titled(store, "undone")) == 0


def test_flush_makes_a_write_visible_to_the_next_read_before_the_commit(store):
    with store.context() as repository:
        note = Note(title="flushed")
        repository.save(note)
        repository.flush()

        assert repository.session.get(Note, note.id) is not None


def test_a_lock_is_refused_outside_a_unit_of_work(store):
    with pytest.raises(ContractViolation, match="for_update only means something inside"):
        store.get(Thing, "th_0001", for_update=True)
    with pytest.raises(ContractViolation, match="for_update"):
        store.search(Things, for_update=True)


def test_a_lock_inside_a_unit_of_work_reads_the_same_rows(store):
    """SQLite has no `FOR UPDATE` and ignores it; what is pinned is that the call is legal
    and answers the same thing — the lock itself is a Postgres matter."""
    with store.context() as repository:
        one = repository.get(Thing, "th_0001", for_update=True, skip_locked=True)
        page = repository.search(
            Things, Criteria(pagination=Pagination(limit=3)), for_update=True
        )

    assert one is not None and one.thing_id == "th_0001"
    assert len(page) == 3


def test_fetch_all_hands_the_whole_set_to_the_in_memory_algebra(store):
    everything = store.fetch_all(Things, Criteria(pagination=Pagination(limit=4)))

    assert len(everything) == ROW_COUNT
    assert everything.count is not None and everything.count.exact
    assert everything.cursor is None
    assert not everything.is_partial
    assert everything.sum_by(lambda thing: thing.size) == sum(n % 5 for n in range(ROW_COUNT))
    assert everything.meta is not None and everything.meta.aggregate == "Thing"


def test_the_engine_options_are_the_providers(tmp_path):
    """Whatever `create_engine` takes passes through: the framework picks no pool."""
    database = Database(f"sqlite:///{tmp_path}/tuned.sqlite3", pool_pre_ping=True)

    assert database.engine.pool._pre_ping is True  # type: ignore[attr-defined]


def test_a_provider_registers_the_grain_translation_for_its_engine():
    def by_hand(column, grain):
        return func.my_trunc(column, grain)

    register_grain_translator("acmedb", by_hand)
    try:
        rendered = str(
            grouping_column(Thing, Level(field="made_at", grain="month"), "acmedb")
        )
        assert "my_trunc" in rendered
    finally:
        del GRAIN_TRANSLATORS["acmedb"]

    with pytest.raises(ContractViolation, match="register_grain_translator"):
        grouping_column(Thing, Level(field="made_at", grain="month"), "acmedb")
    assert Operator.GT in Operator
