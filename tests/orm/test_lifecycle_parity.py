"""The threshold, proved to be the same threshold in both stores.

Every divergence this suite has caught was the same shape: a double that answered a question
differently from the engine, so a Feature passed its tests and failed in production. These tests
run one script against both and compare the answers directly, rather than asserting each store
against a number somebody wrote down.
"""

from collections.abc import Callable
from typing import Any

import pytest

from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.criteria.pagination import Pagination
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.repositories import MemoryRepository
from sincpro_framework.ddd.repositories import Repository as Store
from sincpro_framework.ddd.repositories import Rule
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .models import Client, Note, Notes, mapper_registry

WRITES = (
    "before_save",
    "after_save",
    "before_create",
    "after_create",
    "before_update",
    "after_update",
    "before_archive",
    "after_archive",
    "before_remove",
    "after_remove",
)


@pytest.fixture(params=["memory", "sqlalchemy"])
def stores(request) -> Callable[[list[Rule]], Store]:
    """A store of each kind, built the same way, so one script runs against both."""
    if request.param == "memory":
        return lambda rules: MemoryRepository(rules=rules)
    database = Database("sqlite://")
    mapper_registry.metadata.create_all(database.engine)
    return lambda rules: Repository(database, rules=rules)


def watching(entity: type, trail: list[str]) -> Rule:
    """A rule that answers every write moment by naming itself."""
    return Rule(
        entity=entity,
        **{name: (lambda name=name: lambda record: trail.append(name))() for name in WRITES},
    )


def test_a_first_save_is_a_create_in_both_stores(stores):
    trail: list[str] = []
    repository = stores([watching(Note, trail)])

    repository.save(Note(title="a", body="b"))

    assert trail == ["before_save", "before_create", "after_save", "after_create"]


def test_saving_one_that_was_stored_is_an_update_in_both_stores(stores):
    """`after_create` can only fire if the store remembered what it decided before the write:
    by then the aggregate's own version has already been raised, so asking it again would call
    every create an update."""
    trail: list[str] = []
    repository = stores([watching(Note, trail)])
    note = Note(title="a", body="b")
    repository.save(note)
    trail.clear()

    loaded = repository.get(Note, note.id)
    assert loaded is not None
    loaded.title = "changed"
    repository.save(loaded)

    assert trail == ["before_save", "before_update", "after_save", "after_update"]


def test_archiving_is_its_own_pair_around_the_save_it_is(stores):
    """A rule written for `before_save` still sees an archive — it is a write — and one written
    for `before_archive` sees only those."""
    trail: list[str] = []
    repository = stores([watching(Client, trail)])
    client = Client(name="ACME")
    repository.save(client)
    trail.clear()

    repository.archive(client)

    assert trail == [
        "before_archive",
        "before_save",
        "before_update",
        "after_save",
        "after_update",
        "after_archive",
    ]


def test_removing_is_its_own_pair(stores):
    trail: list[str] = []
    repository = stores([watching(Note, trail)])
    note = Note(title="a", body="b")
    repository.save(note)
    trail.clear()

    repository.remove(note)

    assert trail == ["before_remove", "after_remove"]


def test_a_batch_is_refused_whole_in_both_stores(stores):
    """Every before-moment, then the write. Refused halfway, nothing is written and no
    after-moment ran."""
    trail: list[tuple[str, str]] = []

    def refuses_the_second(note: Note) -> None:
        trail.append(("before", note.title))
        if note.title == "b":
            raise ContractViolation("not this one")

    repository = stores(
        [
            Rule(entity=Note, before_save=refuses_the_second),
            Rule(entity=Note, after_save=lambda one: trail.append(("after", one.title))),
        ]
    )

    with pytest.raises(ContractViolation):
        repository.save([Note(title=one, body="x") for one in ("a", "b", "c")])

    assert trail == [("before", "a"), ("before", "b")]
    assert repository.count(Notes).value == 0


def readings(repository: Store) -> dict[str, Any]:
    """What each reading answered, counted the same way on both stores."""
    pages: list[int] = []
    rows: list[int] = []
    repository._rules = (
        Rule(
            entity=Note,
            after_search=lambda page: pages.append(len(page.items)),
            after_read=lambda one: rows.append(1),
        ),
    )
    answers: dict[str, Any] = {}
    for name, run in (
        ("search", lambda: repository.search(Notes)),
        ("fetch_all", lambda: repository.fetch_all(Notes)),
        (
            "stream",
            lambda: list(repository.stream(Notes, Criteria(pagination=Pagination(limit=3)))),
        ),
        ("browse", lambda: repository.browse(Notes, [])),
        ("count", lambda: repository.count(Notes)),
        ("exists", lambda: repository.exists(Notes)),
        ("pluck", lambda: repository.pluck(Notes, "title")),
    ):
        pages.clear()
        rows.clear()
        run()
        answers[name] = (list(pages), len(rows))
    return answers


def test_every_reading_fires_the_same_moments_in_both_stores():
    """`after_search` once for each page a caller receives, `after_read` once for each aggregate
    in it — and nothing at all for a reading the engine answers in SQL without building
    anything. Compared store against store rather than against a number, because the number is
    not the point: agreeing is.
    """
    database = Database("sqlite://")
    mapper_registry.metadata.create_all(database.engine)
    engine, double = Repository(database), MemoryRepository()
    for index in range(7):
        for repository in (engine, double):
            repository.save(Note(title=f"n{index}", body="x"))

    assert readings(engine) == readings(double)


def test_the_readings_answer_what_they_should_beyond_merely_agreeing():
    """Agreeing on nothing would also pass the test above."""
    repository = MemoryRepository()
    for index in range(7):
        repository.save(Note(title=f"n{index}", body="x"))

    answered = readings(repository)
    assert answered["search"] == ([7], 7)
    assert answered["stream"] == ([3, 3, 1], 7)  # one page each, seven rows once
    assert answered["count"] == ([], 0)  # computed, never built
    assert answered["pluck"] == ([], 0)
