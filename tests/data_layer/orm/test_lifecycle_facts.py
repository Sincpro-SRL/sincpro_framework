"""A tracked record's life as facts, the same in both stores: removed, it records its deletion;
archived, its archiving; brought back, its restoring. Each is stored in its context's event
table — the classes the context declared once on its entities' base — so the record's history
holds it after the row is gone.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import Column, Text
from sqlalchemy.orm import registry

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.orm import (
    Repository,
    map_aggregates,
    map_events,
    template_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    ArchivableMixin,
    ChangeTrackingMixin,
    DomainEvent,
    Entity,
    EntityArchived,
    EntityDeleted,
    EntityEdits,
    EntityRestored,
    EntityUpdated,
    Purge,
    history_of,
)
from sincpro_framework.ddd.criteria import All, Condition, Criteria, Operator
from sincpro_framework.ddd.entity.entity import utc_now
from sincpro_framework.ddd.repositories import IRepository
from sincpro_framework.sincpro_abstractions import Preview

from .engines import fresh


@dataclass(kw_only=True)
class ShopEvent(DomainEvent):
    name = "tests.facts.v1.event"


@dataclass(kw_only=True)
class ShopUpdated(EntityUpdated, ShopEvent):
    name = "tests.facts.v1.updated"


@dataclass(kw_only=True)
class ShopDeleted(EntityDeleted, ShopEvent):
    name = "tests.facts.v1.deleted"


@dataclass(kw_only=True)
class ShopArchived(EntityArchived, ShopEvent):
    name = "tests.facts.v1.archived"


@dataclass(kw_only=True)
class ShopRestored(EntityRestored, ShopEvent):
    name = "tests.facts.v1.restored"


class ShopEntity(ChangeTrackingMixin, Entity):
    """The context's base: its facts named once, for every entity of it."""

    change_event = ShopUpdated
    delete_event = ShopDeleted
    archive_event = ShopArchived
    restore_event = ShopRestored


@dataclass
class Product(ArchivableMixin, ShopEntity):
    name: str = ""


facts = registry()
product_table = entity_table(
    "lf_product",
    facts.metadata,
    Column("name", Text, nullable=False),
    *template_table.archive_columns(),
)
shop_events = template_table.event_table("lf_events", facts.metadata)
map_aggregates(facts, {Product: product_table})
map_events(facts, ShopEvent, shop_events)


@pytest.fixture(params=["memory", "sqlalchemy"])
def store(request, engine_url: str) -> Callable[[], IRepository]:
    """One store of each kind, so one script runs against both."""
    if request.param == "memory":
        return MemoryRepository
    return lambda: Repository(fresh(engine_url, facts.metadata))


def history(repository: IRepository, product: Product) -> list[str]:
    stored = repository.search(ShopEvent, history_of(product))
    return [type(one).__name__ for one in sorted(stored.items, key=lambda one: one.id)]


def test_a_removed_record_leaves_its_deletion_in_the_history(store):
    repository = store()
    product = Product(name="mug")
    repository.save(product)

    repository.remove(repository.get(Product, product.id))

    assert repository.get(Product, product.id) is None
    assert history(repository, product) == ["ShopDeleted"]


def archived(identity: str) -> Criteria:
    """The record by its id, archived — a reading names `archived_at` to see it."""
    return Criteria(
        selection=All(
            all=[
                Condition(field="id", value=identity),
                Condition(field="archived_at", operator=Operator.IS_NULL, value=False),
            ]
        )
    )


def test_archiving_and_bringing_back_are_facts_and_no_update(store):
    repository = store()
    product = Product(name="mug")
    repository.save(product)

    repository.archive(repository.get(Product, product.id))
    assert repository.get(Product, product.id) is None  # out of every reading
    with repository.context() as unit:
        held = unit.search(Product, archived(product.id)).items[0]
        held.restore()
        unit.save(held)

    assert repository.get(Product, product.id) is not None
    assert history(repository, product) == ["ShopArchived", "ShopRestored"]


class CommandPurgeProducts(Purge[Product]):
    pass


def test_a_purge_deletes_the_long_archived_and_leaves_each_deletion_in_the_history(store):
    repository = store()
    old = Product(name="old", archived_at=utc_now() - timedelta(days=40))
    recent = Product(name="recent", archived_at=utc_now() - timedelta(days=5))
    repository.save([old, recent])
    shop = UseFramework("facts-shop", log_after_execution=False)
    shop.add_dependency("repository", repository)

    @shop.feature(CommandPurgeProducts)
    class ProductForms(EntityEdits[Product]):
        pass

    preview = shop(CommandPurgeProducts(older_than=timedelta(days=30), dry_run=True))
    shop(CommandPurgeProducts(older_than=timedelta(days=30)))

    assert isinstance(preview, Preview) and preview.data == {"count": 1}
    left = repository.search(Product, archived(recent.id)).items
    assert [one.name for one in left] == ["recent"]
    assert history(repository, old) == ["ShopDeleted"]
