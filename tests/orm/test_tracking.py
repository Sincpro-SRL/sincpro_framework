"""The same `ChangeTrackingMixin` contract `tests/ddd/entity/test_tracking.py` proves against
`MemoryRepository`, proved here against the real SQLAlchemy `Repository`.

Underneath they are not the same mechanism, and these tests are where that matters. The double
compares against a baseline it took when it handed the aggregate out, so it sees the `save()`
calls it was told about. This store asks the engine what it is about to write, at `before_flush`,
so it sees **every** route that reaches the database — including the ones that never call
`save()` and the ones that never touch a repository at all.
"""

from sincpro_framework.ddd.criteria import Criteria, Grouping, Level
from sincpro_framework.ddd.criteria.pagination import Pagination
from sincpro_framework.ddd.entity import EntityUpdated
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .models import TrackedNote, TrackedNotes


def test_nothing_is_recorded_on_the_first_save(database: Database):
    repository = Repository(database)
    note = TrackedNote(title="first")

    repository.save(note)

    assert note.pull_events() == []


def test_one_event_carries_every_field_that_changed(database: Database):
    repository = Repository(database)
    note = TrackedNote(title="before", body="old")
    repository.save(note)

    loaded = repository.get(TrackedNote, note.id)
    assert loaded is not None
    loaded.title = "after"
    loaded.body = "new"
    repository.save(loaded)

    events = loaded.pull_events()
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, EntityUpdated)
    assert event.changes == {"title": ("before", "after"), "body": ("old", "new")}


def test_excluded_field_never_shows_up_in_the_diff(database: Database):
    repository = Repository(database)
    note = TrackedNote(title="x")
    repository.save(note)

    loaded = repository.get(TrackedNote, note.id)
    assert loaded is not None
    loaded.render_cache = "rebuilt"
    repository.save(loaded)

    assert loaded.pull_events() == []


def test_a_row_read_inside_a_unit_of_work_is_snapshotted_too(database: Database):
    repository = Repository(database)
    note = TrackedNote(title="before")
    repository.save(note)

    with repository.context() as unit:
        loaded = unit.get(TrackedNote, note.id)
        assert loaded is not None
        loaded.title = "after"
        unit.save(loaded)

    events = loaded.pull_events()
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, EntityUpdated)
    assert event.changes == {"title": ("before", "after")}


def test_a_row_from_a_grouped_page_is_snapshotted_like_any_other(database: Database):
    """The one read path that builds its own page instead of going through `_page`: a grouped
    search with a page per group. A row handed back without a baseline would take a change and
    emit nothing — silently."""
    repository = Repository(database)
    repository.save(TrackedNote(title="before", body="alpha"))

    page = repository.search(
        TrackedNotes,
        Criteria(
            pagination=Pagination(limit=5),
            grouping=Grouping(group_by=(Level(field="body"),)),
        ),
    )

    found = page.items[0]
    found.title = "after"
    repository.save(found)

    events = found.pull_events()
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, EntityUpdated)
    assert event.changes == {"title": ("before", "after")}


# --- the routes a baseline on the aggregate could not see ----------------------------------


def _stored(repository: Repository, title: str = "before") -> TrackedNote:
    """A note already in the database, with its first-save events pulled off."""
    note = TrackedNote(title=title, body="a")
    repository.save(note)
    note.pull_events()
    return note


def test_a_change_made_inside_a_unit_of_work_without_save_is_still_recorded(
    database: Database,
):
    """The idiomatic write: load inside `context()`, change it, let the block commit. The
    session persists it whether or not anybody called `save()` — so an event that only fired on
    `save()` left the row changed and the audit silent."""
    repository = Repository(database)
    note = _stored(repository)

    with repository.context() as unit:
        loaded = unit.get(TrackedNote, note.id)
        assert loaded is not None
        loaded.title = "changed without save"

    stored = repository.get(TrackedNote, note.id)
    assert stored is not None and stored.title == "changed without save"
    [recorded] = loaded.pull_events()
    assert isinstance(recorded, EntityUpdated)
    assert recorded.changes == {"title": ("before", "changed without save")}


def test_a_change_made_without_any_repository_at_all_is_still_recorded(database: Database):
    """A use case holding a plain session, a script, a migration. Registered on the session,
    this reaches them; registered on the repository it could not."""
    repository = Repository(database)
    note = _stored(repository)

    with database.session() as session:
        loaded = session.get(TrackedNote, note.id)
        assert loaded is not None
        loaded.title = "changed without a repository"

    stored = repository.get(TrackedNote, note.id)
    assert stored is not None and stored.title == "changed without a repository"
    [recorded] = loaded.pull_events()
    assert isinstance(recorded, EntityUpdated)
    assert recorded.changes == {"title": ("before", "changed without a repository")}


def test_saving_explicitly_records_exactly_one_event(database: Database):
    """Both halves used to be able to fire; one event per aggregate per flush, not two."""
    repository = Repository(database)
    note = _stored(repository)

    with repository.context() as unit:
        loaded = unit.get(TrackedNote, note.id)
        assert loaded is not None
        loaded.title = "changed with save"
        unit.save(loaded)

    assert len(loaded.pull_events()) == 1


def test_a_write_that_changes_nothing_tracked_records_nothing(database: Database):
    """`session.dirty` holds anything the engine is *considering*. An excluded field moving,
    or a field set to the value it already held, is not a change worth a fact."""
    repository = Repository(database)
    note = _stored(repository)

    with repository.context() as unit:
        loaded = unit.get(TrackedNote, note.id)
        assert loaded is not None
        loaded.render_cache = "recomputed"  # tracked=False
        loaded.title = "before"  # set to what it already was

    assert loaded.pull_events() == []
