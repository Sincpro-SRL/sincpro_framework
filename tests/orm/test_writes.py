"""The write door, and the two races it refuses to lose in silence.

Reads are generic; writes take the aggregate. What is pinned here is the contract a use case
leans on without checking: a new aggregate is inserted and a loaded one is updated, `version`
and `updated_at` move without anybody touching them, two callers that both loaded version one
cannot both write version two, and a unit of work either commits everything or nothing.
"""

import pytest

from sincpro_framework.ddd.criteria import Condition, Criteria
from sincpro_framework.ddd.criteria.pagination import Pagination
from sincpro_framework.ddd.exceptions import DuplicateAggregate, StaleAggregate

from .models import ROW_COUNT, Note, Notes, Thing, Things, a_thing


def titled(store, title: str) -> Notes:
    return store.search(Notes, Criteria(where=Condition(field="title", value=title)))


def test_get_answers_the_record_or_nothing(store):
    assert store.get(Thing, "th_0001").name == "thing 1"
    assert store.get(Things, "th_0001").name == "thing 1"
    assert store.get(Thing, "nope") is None


def test_a_new_entity_is_inserted_with_its_first_version(store):
    note = Note(title="first")
    assert note.is_new

    store.save(note)

    stored = store.get(Note, note.id)
    assert stored is not None and stored.title == "first"
    assert stored.version == 1 and not stored.is_new
    assert stored.created_at is not None and stored.updated_at is None


def test_a_loaded_entity_is_updated_and_stamped(store):
    note = Note(title="before")
    store.save(note)

    loaded = store.get(Note, note.id)
    loaded.title = "after"
    store.save(loaded)

    again = store.get(Note, note.id)
    assert again.title == "after"
    assert again.version == 2
    assert again.updated_at is not None and again.updated_at >= again.created_at


def test_a_save_that_changed_nothing_is_not_stamped(store):
    note = Note(title="same")
    store.save(note)

    loaded = store.get(Note, note.id)
    loaded.title = "same"
    store.save(loaded)

    assert store.get(Note, note.id).updated_at is None


def test_the_second_writer_of_the_same_version_is_refused(store):
    """Two callers read version one; the first writes two; the second is told, not obeyed."""
    note = Note(title="contested")
    store.save(note)
    first = store.get(Note, note.id)
    second = store.get(Note, note.id)

    first.title = "first wins"
    store.save(first)

    second.title = "second loses"
    with pytest.raises(StaleAggregate, match="changed since it was read"):
        store.save(second)
    assert store.get(Note, note.id).title == "first wins"


def test_an_aggregate_without_a_version_is_still_updated(store):
    """The convention adds the check; the layer does not demand the convention."""
    thing = store.get(Thing, "th_0002")
    thing.name = "renamed"
    store.save(thing)

    assert store.get(Thing, "th_0002").name == "renamed"
    thing.name = "thing 2"
    store.save(thing)


def test_a_record_built_by_hand_with_a_taken_id_is_a_duplicate(store):
    """Not a merge: the update path is to read the record and change it."""
    with pytest.raises(DuplicateAggregate, match="collides"):
        store.save(
            Thing(
                thing_id="th_0001", name="twin", size=0, tags=[], made_at=a_thing(1).made_at
            )
        )
    assert store.get(Thing, "th_0001").name == "thing 1"


def test_remove_deletes_what_was_read(store):
    note = Note(title="doomed")
    store.save(note)

    store.remove(store.get(Note, note.id))

    assert store.get(Note, note.id) is None


def test_removing_a_stale_entity_is_refused_too(store):
    note = Note(title="guarded")
    store.save(note)
    stale = store.get(Note, note.id)
    fresh = store.get(Note, note.id)
    fresh.title = "moved on"
    store.save(fresh)

    with pytest.raises(StaleAggregate):
        store.remove(stale)
    assert store.get(Note, note.id) is not None


def test_a_unit_of_work_commits_everything_or_nothing(store):
    with pytest.raises(RuntimeError):
        with store.context() as repository:
            repository.save(Note(title="never"))
            repository.save(Note(title="never"))
            raise RuntimeError("halfway")

    assert len(titled(store, "never")) == 0

    with store.context() as repository:
        repository.save(Note(title="both"))
        repository.save(Note(title="both"))

    assert len(titled(store, "both")) == 2


def test_a_unit_of_work_reads_what_it_wrote_before_committing(store):
    """One session: the read inside sees the write inside, and the same engine surface
    answers — search, get, count — bound to that session."""
    with store.context() as repository:
        note = Note(title="visible inside")
        repository.save(note)

        assert repository.get(Note, note.id) is not None
        assert (
            len(
                repository.search(
                    Notes, Criteria(where=Condition(field="title", value="visible inside"))
                )
            )
            == 1
        )
        with repository.context() as nested:
            assert nested is repository


def test_the_stream_walks_every_page_once(store):
    pages = list(store.stream(Things, Criteria(pagination=Pagination(limit=4))))
    seen = [one for page in pages for one in page.ids]

    assert len(pages) == -(-ROW_COUNT // 4)
    assert len(seen) == ROW_COUNT and len(set(seen)) == ROW_COUNT
    assert pages[-1].cursor is None
