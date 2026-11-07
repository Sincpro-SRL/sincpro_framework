"""A relation with many children: read per parent without reading them all, counted as its
node asks, and never brought whole into memory by touching it.

Runs on every engine the suite has (`engine_url`): on PostgreSQL the node is a `LATERAL` join
that stops at the page, elsewhere a window — the answers must be the same.

    Ana     60 invoices, 5 labels
    Beto     3 invoices
    Ciro     none
"""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from sqlalchemy import Column, ForeignKey, Integer, Table, Text, event
from sqlalchemy.orm import registry
from structlog.testing import capture_logs

from sincpro_framework.data_layer.orm import TableRelation, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.ddd.criteria import (
    Condition,
    CountMode,
    Criteria,
    Operator,
    Pagination,
    Sort,
    Specification,
)
from sincpro_framework.ddd.entity import Entity, relations
from sincpro_framework.ddd.entity.entity_collection import Count, EntityCollection

from .engines import fresh


@dataclass
class Label(Entity):
    text: str


@dataclass
class Invoice(Entity):
    customer_id: str
    number: int


@dataclass
class Customer(Entity):
    name: str
    invoices: list[Invoice] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)


class Customers(EntityCollection[Customer]):
    pass


shop = registry()
customer_table = entity_table("customer", shop.metadata, Column("name", Text, nullable=False))
label_table = entity_table("label", shop.metadata, Column("text", Text, nullable=False))
invoice_table = entity_table(
    "invoice",
    shop.metadata,
    Column("customer_id", Text, ForeignKey("customer.id"), nullable=False),
    Column("number", Integer, nullable=False),
)
customer_label = Table(
    "customer_label",
    shop.metadata,
    Column("customer_id", Text, ForeignKey("customer.id"), primary_key=True),
    Column("label_id", Text, ForeignKey("label.id"), primary_key=True),
)
map_aggregates(
    shop,
    {Customer: customer_table, Invoice: invoice_table, Label: label_table},
    relations={
        Customer: {
            "invoices": TableRelation.foreign_key(
                Invoice, identified_by="customer_id", owned=False
            ),
            "labels": TableRelation.many_to_many(
                Label,
                through=customer_label,
                this_key="customer_id",
                related_key="label_id",
            ),
        }
    },
)


@pytest.fixture
def store(engine_url) -> Generator[dict[str, Any]]:
    database = fresh(engine_url, shop.metadata)
    repository = Repository(database)
    ana, beto, ciro = Customer(name="Ana"), Customer(name="Beto"), Customer(name="Ciro")
    labels = [Label(text=f"label {n}") for n in range(5)]
    invoices = [Invoice(customer_id=ana.id, number=n) for n in range(1, 61)]
    invoices += [Invoice(customer_id=beto.id, number=n) for n in range(1, 4)]
    with repository.context() as unit:
        unit.session.add_all([ana, beto, ciro, *labels, *invoices])
        unit.flush()
        unit.session.execute(
            customer_label.insert(),
            [{"customer_id": ana.id, "label_id": label.id} for label in labels],
        )
    yield {"repository": repository, "ana": ana, "beto": beto, "ciro": ciro}
    database.engine.dispose()


@contextmanager
def statements(repository: Repository) -> Generator[list[str]]:
    seen: list[str] = []

    def record(connection, cursor, statement, parameters, context, many) -> None:
        seen.append(statement)

    event.listen(repository.database.engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(repository.database.engine, "before_cursor_execute", record)


def customers_with(repository: Repository, **nodes: Criteria) -> dict[str, Customer]:
    page = repository.search(
        Customers, Criteria(order=Sort.parse("name"), specification=Specification(nodes))
    )
    return {customer.name: customer for customer in page}


def page_of(related: Any) -> EntityCollection:
    return cast(EntityCollection, related)


def got[T](repository: Repository, kind: type[T], identity: Any) -> T:
    """The record under `identity`, which the fixture stored."""
    record = repository.get(kind, identity)
    assert record is not None
    return record


def newest(limit: int, **options: Any) -> Criteria:
    return Criteria(
        order=Sort.parse("-number"), pagination=Pagination(limit=limit), **options
    )


def test_each_parent_gets_its_page_its_count_and_a_cursor_in_one_statement(store):
    """Ana's five newest of sixty, Beto's three, Ciro's none — one statement for the node,
    whatever the engine: a LATERAL join on PostgreSQL, a window elsewhere."""
    repository: Repository = store["repository"]

    with statements(repository) as seen:
        customers = customers_with(repository, invoices=newest(5))

    ana, beto, ciro = (page_of(customers[name].invoices) for name in ("Ana", "Beto", "Ciro"))
    assert [invoice.number for invoice in ana] == [60, 59, 58, 57, 56]
    assert ana.count == Count(value=60, exact=True)
    assert ana.cursor is not None
    assert [invoice.number for invoice in beto] == [3, 2, 1]
    assert beto.count == Count(value=3, exact=True) and beto.cursor is None
    assert len(ciro) == 0 and ciro.count == Count(value=0, exact=True)
    node = [s for s in seen if "invoice" in s.lower() and "customer.name" not in s.lower()]
    assert len(node) == 1
    if repository.database.engine.dialect.name == "postgresql":
        assert "LATERAL" in node[0].upper()
    else:
        assert "row_number" in node[0].lower()


def test_the_cursor_goes_on_inside_that_parent(store):
    repository: Repository = store["repository"]
    first = page_of(customers_with(repository, invoices=newest(5))["Ana"].invoices)

    rest = repository.search(
        Invoice,
        newest(
            5, selection=Condition(field="customer_id", value=store["ana"].id)
        ).resuming_from(first.cursor),
    )

    assert [invoice.number for invoice in rest] == [55, 54, 53, 52, 51]


def test_past_the_cap_the_count_is_a_floor_and_exact_only_when_asked(store, monkeypatch):
    """With the cap at ten, Ana's sixty read as «at least ten»; an exact count is one request
    away, and none at all keeps the cursor that says there is more."""
    monkeypatch.setattr(relations, "RELATION_COUNT_CAP", 10)
    repository: Repository = store["repository"]

    capped = page_of(customers_with(repository, invoices=newest(5))["Ana"].invoices)
    exact = page_of(
        customers_with(repository, invoices=newest(5, count=CountMode.EXACT))["Ana"].invoices
    )
    none = page_of(
        customers_with(repository, invoices=newest(5, count=CountMode.NONE))["Ana"].invoices
    )
    beto = page_of(customers_with(repository, invoices=newest(5))["Beto"].invoices)

    assert capped.count == Count(value=10, exact=False)
    assert capped.is_partial
    assert exact.count == Count(value=60, exact=True)
    assert none.count is None and none.cursor is not None
    assert beto.count == Count(value=3, exact=True)


def test_a_floor_is_never_below_the_page_it_came_with(store, monkeypatch):
    monkeypatch.setattr(relations, "RELATION_COUNT_CAP", 10)
    repository: Repository = store["repository"]

    ana = page_of(customers_with(repository, invoices=newest(20))["Ana"].invoices)

    assert len(ana) == 20
    assert ana.count == Count(value=20, exact=False)


def test_the_node_filter_narrows_the_rows_and_the_count_alike(store):
    repository: Repository = store["repository"]
    over_fifty = Condition(field="number", operator=Operator.GT, value=50)

    ana = page_of(
        customers_with(repository, invoices=newest(3, selection=over_fifty))["Ana"].invoices
    )

    assert [invoice.number for invoice in ana] == [60, 59, 58]
    assert ana.count == Count(value=10, exact=True)


def test_a_many_to_many_is_cut_and_counted_per_parent_the_same_way(store):
    repository: Repository = store["repository"]

    customers = customers_with(
        repository, labels=Criteria(order=Sort.parse("text"), pagination=Pagination(limit=2))
    )

    ana = page_of(customers["Ana"].labels)
    assert [label.text for label in ana] == ["label 0", "label 1"]
    assert ana.count == Count(value=5, exact=True) and ana.cursor is not None
    assert len(page_of(customers["Beto"].labels)) == 0


def test_inside_a_unit_of_work_a_small_relation_is_read_whole(store):
    repository: Repository = store["repository"]

    with repository.context() as unit:
        beto = got(unit, Customer, store["beto"].id)
        invoices = page_of(beto.invoices)

        assert sorted(invoice.number for invoice in invoices) == [1, 2, 3]
        assert invoices.count == Count(value=3, exact=True)
        assert not invoices.is_partial


def test_a_relation_read_whole_past_the_warning_is_read_all_and_said(store, monkeypatch):
    """Ana's sixty invoices against a warning at 25: touching the relation reads all sixty — the
    framework reads what it is asked to — and the log says how many and how to ask for less.
    """
    monkeypatch.setattr(relations, "WHOLE_WARNING", 25)
    repository: Repository = store["repository"]

    with capture_logs() as logs:
        with repository.context() as unit:
            ana = got(unit, Customer, store["ana"].id)
            invoices = page_of(ana.invoices)

            assert len(invoices) == 60 and invoices.count == Count(value=60, exact=True)

    said = [one["event"] for one in logs if "was read whole" in one["event"]]
    assert len(said) == 1 and "Customer.invoices was read whole: 60 Invoice" in said[0]


def test_inside_a_unit_of_work_a_page_left_on_the_record_gives_way_to_the_whole(store):
    repository: Repository = store["repository"]

    with repository.context() as unit:
        ana = unit.search(
            Customers,
            Criteria(
                selection=Condition(field="id", value=store["ana"].id),
                specification=Specification({"invoices": newest(5)}),
            ),
        ).ensure_one()

        assert len(page_of(ana.invoices)) == 60


def test_a_foreign_key_declared_owned_false_is_published_as_a_reference():
    from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import (
        ModelIntrospection,
    )
    from sincpro_framework.ddd.metamodel import contract_of

    ModelIntrospection.describe.cache_clear()
    fields = ModelIntrospection.describe(Customer).fields
    invoices = contract_of(Customer)["properties"]["invoices"]

    assert fields["invoices"].reference is True
    assert fields["labels"].reference is False
    assert invoices["x-sincpro"]["reference"] is True
    assert invoices["properties"]["items"] == {
        "type": "array",
        "items": {"$ref": "#/$defs/Invoice"},
    }
