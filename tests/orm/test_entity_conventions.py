"""The two conventions an aggregate opts into: who wrote it, and being put away rather than
deleted.

Both are answered by the adapter and by nobody else: a use case never writes `created_by` and
never remembers to exclude the archived, which is the whole point of them being conventions.
"""

from datetime import datetime

import pytest

from sincpro_framework.ddd.criteria import Condition, Criteria, Operator
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .models import Client, Clients, mapper_registry


@pytest.fixture
def acting() -> dict[str, str | None]:
    """Who the process says is writing, changed between calls the way a request does."""
    return {"user": "ana"}


@pytest.fixture
def clients(acting) -> Repository:
    database = Database("sqlite://", actor=lambda: acting["user"])
    mapper_registry.metadata.create_all(database.engine)
    return Repository(database)


def test_who_wrote_it_is_stamped_on_the_insert(clients, acting):
    client = Client(name="Labs")

    clients.save(client)

    stored = clients.get(Client, client.id)
    assert stored is not None
    assert stored.created_by == "ana" and stored.updated_by is None


def test_who_changed_it_is_stamped_on_the_update(clients, acting):
    client = Client(name="Labs")
    clients.save(client)

    acting["user"] = "bo"
    with clients.context() as unit:
        stored = unit.get(Client, client.id)
        assert stored is not None
        stored.city = "Cochabamba"
        unit.save(stored)

    after = clients.get(Client, client.id)
    assert after is not None
    assert after.created_by == "ana" and after.updated_by == "bo"
    assert after.updated_at is not None


def test_nobody_writing_leaves_the_two_alone(clients, acting):
    """A request with no user behind it writes `None` rather than failing the transaction."""
    acting["user"] = None
    client = Client(name="Anonymous")

    clients.save(client)

    stored = clients.get(Client, client.id)
    assert stored is not None and stored.created_by is None


def test_removing_an_archivable_puts_it_away(clients):
    client = Client(name="Gone")
    clients.save(client)

    clients.remove(client)

    assert clients.get(Client, client.id) is None  # out of every ordinary reading
    with clients.context() as unit:
        row = unit.session.get(Client, client.id)
        assert row is not None and isinstance(row.archived_at, datetime)


def test_a_reading_leaves_the_archived_out_unless_it_asks(clients):
    live, gone = Client(name="Live"), Client(name="Gone")
    clients.save_all([live, gone])
    clients.remove(gone)

    assert [one.name for one in clients.search(Clients)] == ["Live"]
    assert clients.count(Clients).value == 1

    archived = clients.search(
        Clients,
        Criteria(
            where=Condition(field="archived_at", operator=Operator.IS_NULL, value=False)
        ),
    )
    assert [one.name for one in archived] == ["Gone"]

    both = clients.search(
        Clients,
        Criteria(where=Condition(field="archived_at", operator=Operator.IS_NULL, value=True)),
    )
    assert [one.name for one in both] == ["Live"]


def test_archiving_twice_keeps_the_first_moment(clients):
    client = Client(name="Twice")
    clients.save(client)
    clients.remove(client)
    first = client.archived_at

    clients.remove(client)

    assert client.archived_at == first


def test_restoring_brings_it_back_to_every_reading(clients):
    client = Client(name="Back")
    clients.save(client)
    clients.remove(client)

    client.restore()
    clients.save(client)

    assert [one.name for one in clients.search(Clients)] == ["Back"]


def test_purge_deletes_what_remove_would_only_archive(clients):
    client = Client(name="Really gone")
    clients.save(client)

    clients.purge(client)

    with clients.context() as unit:
        assert unit.session.get(Client, client.id) is None
