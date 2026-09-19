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
        store.save(written)

    inserts = [one for one in statements if one.lstrip().upper().startswith("INSERT")]
    assert len(inserts) == 1  # one statement, executemany — not one per record
    assert store.count(Notes).value == 50
    assert all(note.version == 1 for note in written)


def test_a_batch_of_nothing_touches_the_database(store: Repository, queries_run):
    with queries_run() as statements:
        store.save([])
        store.remove([])

    assert statements == []


def test_a_batch_updates_what_was_loaded_and_inserts_what_was_not(store: Repository):
    first = notes(3, "old")
    store.save(first)
    loaded = list(store.fetch_all(Notes))
    for note in loaded:
        note.body = "changed"

    store.save([*loaded, *notes(2, "new")])

    assert store.count(Notes).value == 5
    assert (
        store.count(Notes, Criteria(where=Condition(field="body", value="changed"))).value
        == 3
    )
    assert all(note.version == 2 for note in store.fetch_all(Notes) if note.body == "changed")


def test_one_stale_record_undoes_the_whole_batch(store: Repository):
    written = notes(3)
    store.save(written)
    mine = list(store.fetch_all(Notes))
    theirs = list(store.fetch_all(Notes))
    for note in mine:
        note.body = "mine"
    store.save(mine)

    for note in theirs:
        note.body = "theirs"
    with pytest.raises(StaleAggregate):
        store.save(theirs)

    assert all(note.body == "mine" for note in store.fetch_all(Notes))


def test_a_batch_removal_deletes_every_record(store: Repository):
    written = notes(4)
    store.save(written)

    store.remove(list(store.fetch_all(Notes)))

    assert store.count(Notes).value == 0


def test_a_unit_of_work_takes_the_batch_with_it(store: Repository):
    with pytest.raises(RuntimeError):
        with store.context() as unit:
            unit.save(notes(10))
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


def test_one_save_takes_one_or_several_and_several_are_still_one_flush(store, queries_run):
    """One `save` for one aggregate or many must not have cost the batch its single round trip."""
    with queries_run() as statements:
        store.save(notes(50))

    inserts = [one for one in statements if one.lstrip().upper().startswith("INSERT")]
    assert len(inserts) == 1
    assert len(store.fetch_all(Notes)) == 50


def test_one_save_takes_a_page_straight_back(store):
    store.save(notes(3))
    page = store.search(Notes)

    for one in page:
        one.body = "rewritten"
    store.save(page)  # what the reading answered, handed straight to the write

    assert {one.body for one in store.fetch_all(Notes)} == {"rewritten"}


def test_one_remove_takes_one_or_several(store):
    store.save(notes(4))
    everything = list(store.fetch_all(Notes))

    store.remove(everything[0])
    store.remove(everything[1:])

    assert len(store.fetch_all(Notes)) == 0


def test_updating_a_batch_costs_one_statement_per_row_and_the_docstring_says_so(
    store: Repository, queries_run
):
    """The other half of the batch story, and the one that surprises people. Inserting many is
    one statement; updating many is one statement *each*, because the version check has to read
    the affected row count back per row to know whether somebody moved it first — which is
    exactly what a batched statement does not report. This pins the cost so the claim in
    `save()`'s docstring cannot quietly stop being true."""
    written = notes(20)
    store.save(written)
    for note in written:
        note.title = f"{note.title} changed"

    with queries_run() as statements:
        store.save(written)

    updates = [one for one in statements if one.lstrip().upper().startswith("UPDATE")]
    assert len(updates) == 20  # one per row: the price of StaleAggregate, not a defect


def test_a_batch_that_loses_a_race_does_not_name_a_record_it_guessed(store: Repository):
    """One flush writes the whole batch and the engine does not say which record lost, so
    naming the first one sent people to read the wrong aggregate. The batch is described for
    what it is, and the engine's own message — which does name the table — is carried through.
    """
    first, second = notes(2)
    store.save([first, second])

    somebody_else = Repository(store.database)
    theirs = somebody_else.get(Note, second.id)
    assert theirs is not None
    theirs.title = "moved first"
    somebody_else.save(theirs)

    second.title = "mine"
    with pytest.raises(StaleAggregate) as refused:
        store.save([first, second])

    said = str(refused.value)
    assert "one of 2 Note" in said  # not "Note", which would read as a single named record
    assert "note" in said  # the engine's message, which names the table that actually failed


def test_one_record_losing_a_race_is_still_named_plainly(store: Repository):
    [note] = notes(1)
    store.save(note)

    somebody_else = Repository(store.database)
    theirs = somebody_else.get(Note, note.id)
    assert theirs is not None
    theirs.title = "moved first"
    somebody_else.save(theirs)

    note.title = "mine"
    with pytest.raises(StaleAggregate, match="^Note changed since it was read"):
        store.save(note)
