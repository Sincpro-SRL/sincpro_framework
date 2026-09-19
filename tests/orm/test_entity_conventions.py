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


def test_archiving_puts_it_away(clients):
    client = Client(name="Gone")
    clients.save(client)

    clients.archive(client)

    assert clients.get(Client, client.id) is None  # out of every ordinary reading
    with clients.context() as unit:
        row = unit.session.get(Client, client.id)
        assert row is not None and isinstance(row.archived_at, datetime)


def test_a_reading_leaves_the_archived_out_unless_it_asks(clients):
    live, gone = Client(name="Live"), Client(name="Gone")
    clients.save([live, gone])
    clients.archive(gone)

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
    clients.archive(client)
    first = client.archived_at

    clients.archive(client)

    assert client.archived_at == first


def test_restoring_brings_it_back_to_every_reading(clients):
    client = Client(name="Back")
    clients.save(client)
    clients.archive(client)

    client.restore()
    clients.save(client)

    assert [one.name for one in clients.search(Clients)] == ["Back"]


def test_remove_deletes_what_archive_would_only_put_away(clients):
    client = Client(name="Really gone")
    clients.save(client)

    clients.remove(client)

    with clients.context() as unit:
        assert unit.session.get(Client, client.id) is None


def test_the_actor_can_be_read_off_the_bus_the_way_the_docstring_says():
    """`Database(url, actor=lambda: bus.current_context().get("user.id"))` is what the docstring
    on `Database` and on `AuditedMixin` tells people to write. It was false — `context` is the
    method that *opens* a context, with no `.get` on it — so `created_by` was never wired for
    anyone who followed the documentation. Every other test here supplies its own callable and
    so never touched the documented form.
    """
    from sincpro_framework import UseFramework

    bus = UseFramework("audited", log_after_execution=False)
    database = Database("sqlite://", actor=lambda: bus.current_context().get("user.id"))
    mapper_registry.metadata.create_all(database.engine)
    clients = Repository(database)

    client = Client(name="ACME")
    with bus.context({"user.id": "andres"}):
        clients.save(client)

    stored = clients.get(Client, client.id)
    assert stored is not None
    assert stored.created_by == "andres"


def test_the_context_read_outside_a_block_is_empty_rather_than_raising():
    """A callable wired once at startup is asked on every write, including writes that happen
    outside any request — a background job, a migration. It answers nothing, it does not fail.
    """
    from sincpro_framework import UseFramework

    bus = UseFramework("outside", log_after_execution=False)

    assert bus.current_context().get("user.id") is None


def test_the_context_is_read_only_from_outside():
    """The framework owns that dict; opening a context is what writes to it."""
    from sincpro_framework import UseFramework

    bus = UseFramework("readonly", log_after_execution=False)
    with bus.context({"user.id": "ana"}):
        with pytest.raises(TypeError):
            bus.current_context()["user.id"] = "somebody else"  # type: ignore[index]
