"""After the commit and after the rollback: the moments a write is final, or gone — and the held
reads a commit lets go of, and a rollback keeps."""

import pytest
from sqlalchemy.orm import Session

from sincpro_framework.data_analysis import QueryCache
from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.orm import Database, Repository, invalidate_on_commit

from .models import Note, Thing, a_thing

EVERY_ROW = Criteria.model_validate({"order": [{"field": "id"}]})


def test_after_commit_runs_once_the_write_is_final_and_after_rollback_once_it_is_gone(
    database: Database,
):
    moments: list[str] = []
    database.after_commit(lambda session: moments.append("committed"))
    database.after_rollback(lambda session: moments.append("rolled back"))
    repository = Repository(database)

    repository.save(Note(title="kept"))
    with pytest.raises(RuntimeError):
        with repository.context() as unit:
            unit.save(Note(title="undone"))
            raise RuntimeError("the use case failed")

    assert moments == ["committed", "rolled back"]


def test_the_commit_hooks_answer_the_database_so_several_read_as_one_wiring(
    database: Database,
):
    def nothing(session: Session) -> None:
        return None

    assert database.after_commit(nothing).after_rollback(nothing) is database


def _held(database: Database) -> tuple[Repository, QueryCache]:
    repository = Repository(database)
    cache = invalidate_on_commit(database, QueryCache(), Note)
    repository.save(Note(title="first"))
    return repository, cache


def test_a_committed_write_of_the_aggregate_lets_its_reads_go(database: Database):
    repository, cache = _held(database)
    held = cache.fetch_all(repository, Note, EVERY_ROW)

    repository.save(Note(title="second"))

    assert cache.get(repository, Note, EVERY_ROW) is None
    assert len(cache.fetch_all(repository, Note, EVERY_ROW)) == len(held) + 1


def test_a_rolled_back_write_keeps_what_is_held(database: Database):
    repository, cache = _held(database)
    held = cache.fetch_all(repository, Note, EVERY_ROW)

    with pytest.raises(RuntimeError):
        with repository.context() as unit:
            unit.save(Note(title="undone"))
            raise RuntimeError("the use case failed")

    assert cache.get(repository, Note, EVERY_ROW) is held


def test_a_write_of_another_aggregate_keeps_what_is_held(database: Database):
    repository, cache = _held(database)
    held = cache.fetch_all(repository, Note, EVERY_ROW)

    repository.save(a_thing(900))

    assert cache.get(repository, Note, EVERY_ROW) is held
    assert repository.get(Thing, a_thing(900).thing_id) is not None


def test_a_read_between_the_flush_and_the_commit_is_let_go_at_the_commit(database: Database):
    """Context: invalidating at the flush would lose this race — the read in between caches the
    rows the commit is about to change."""
    repository, cache = _held(database)

    with repository.context() as unit:
        unit.save(Note(title="second"))
        cache.fetch_all(repository, Note, EVERY_ROW)

    assert cache.get(repository, Note, EVERY_ROW) is None
