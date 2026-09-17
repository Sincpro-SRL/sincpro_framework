"""Writing many aggregates at once, and running a unit of work again when it lost a race.

The assertion that matters in the batch is the statement count: a thousand records have to cost
the round trips of a batch and not of a thousand saves, and a batch that fails has to leave
nothing behind.
"""

import pytest

from sincpro_framework.ddd.criteria import Condition, Criteria
from sincpro_framework.ddd.exceptions import ContractViolation, StaleAggregate
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .models import Note, Notes


def notes(how_many: int, prefix: str = "n") -> list[Note]:
    return [Note(title=f"{prefix}-{index}") for index in range(how_many)]


def test_a_batch_writes_every_record_in_one_flush(store: Repository, queries_run):
    written = notes(50)

    with queries_run() as statements:
        store.save_all(written)

    inserts = [one for one in statements if one.lstrip().upper().startswith("INSERT")]
    assert len(inserts) == 1  # one statement, executemany — not one per record
    assert store.count(Notes).value == 50
    assert all(note.version == 1 for note in written)


def test_a_batch_of_nothing_touches_the_database(store: Repository, queries_run):
    with queries_run() as statements:
        store.save_all([])
        store.remove_all([])

    assert statements == []


def test_a_batch_updates_what_was_loaded_and_inserts_what_was_not(store: Repository):
    first = notes(3, "old")
    store.save_all(first)
    loaded = list(store.fetch_all(Notes))
    for note in loaded:
        note.body = "changed"

    store.save_all([*loaded, *notes(2, "new")])

    assert store.count(Notes).value == 5
    assert (
        store.count(Notes, Criteria(where=Condition(field="body", value="changed"))).value
        == 3
    )
    assert all(note.version == 2 for note in store.fetch_all(Notes) if note.body == "changed")


def test_one_stale_record_undoes_the_whole_batch(store: Repository):
    written = notes(3)
    store.save_all(written)
    mine = list(store.fetch_all(Notes))
    theirs = list(store.fetch_all(Notes))
    for note in mine:
        note.body = "mine"
    store.save_all(mine)

    for note in theirs:
        note.body = "theirs"
    with pytest.raises(StaleAggregate):
        store.save_all(theirs)

    assert all(note.body == "mine" for note in store.fetch_all(Notes))


def test_a_batch_removal_deletes_every_record(store: Repository):
    written = notes(4)
    store.save_all(written)

    store.remove_all(list(store.fetch_all(Notes)))

    assert store.count(Notes).value == 0


def test_a_unit_of_work_takes_the_batch_with_it(store: Repository):
    with pytest.raises(RuntimeError):
        with store.context() as unit:
            unit.save_all(notes(10))
            raise RuntimeError("the caller changed its mind")

    assert store.count(Notes).value == 0


def test_a_race_is_run_again_and_the_second_time_it_lands(store: Repository):
    """What a Feature does when two workers touch one aggregate: read again, decide again."""
    store.save(Note(title="counter"))
    attempts: list[int] = []

    def work() -> int:
        attempts.append(1)
        with store.context() as unit:
            note = unit.search(Notes).ensure_one()
            if len(attempts) < 3:
                _steal(store, note.id)  # somebody else wrote while this block was deciding
            note.body = f"attempt {len(attempts)}"
            unit.save(note)
        return len(attempts)

    assert store.retrying(work, wait=0) == 3
    assert store.search(Notes).ensure_one().body == "attempt 3"


def test_a_race_that_never_settles_raises_what_it_lost_to(store: Repository):
    store.save(Note(title="contested"))

    def work() -> None:
        with store.context() as unit:
            note = unit.search(Notes).ensure_one()
            _steal(store, note.id)
            note.body = "mine"
            unit.save(note)

    with pytest.raises(StaleAggregate):
        store.retrying(work, attempts=2, wait=0)


def test_retrying_leaves_anything_else_alone(store: Repository):
    def work() -> None:
        raise ValueError("not a race")

    with pytest.raises(ValueError):
        store.retrying(work, wait=0)

    with pytest.raises(ContractViolation):
        store.retrying(lambda: None, attempts=0)


def _steal(store: Repository, note_id: str) -> None:
    """Another writer moves the row on, in its own transaction."""
    with store.database.session() as session:
        other = session.get(Note, note_id)
        assert other is not None
        other.title = f"{other.title}!"
