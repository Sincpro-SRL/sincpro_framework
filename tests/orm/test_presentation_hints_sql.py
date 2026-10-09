"""The form hints of a mapped class: the definition a database-backed screen receives says the
same as the one read off the class alone, and a hint still refuses no write.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Column, Enum, Numeric, Text
from sqlalchemy.orm import registry

from sincpro_framework import UseFramework
from sincpro_framework.ddd import (
    EntityReads,
    Get,
    Is,
    Presentation,
    ResponseRecord,
    When,
)
from sincpro_framework.ddd.criteria import Operator
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.model_meta import describe_class
from sincpro_framework.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.orm.sqlalchemy.entrypoint.templates import entity_table
from sincpro_framework.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.orm.sqlalchemy.services.data_mapper import map_aggregates
from sincpro_framework.orm.sqlalchemy.services.model_introspection import describe


class SaleState(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


@dataclass
class Sale(Entity):
    customer_id: str
    number: str = "/"
    state: SaleState = SaleState.DRAFT
    amount: Decimal = Decimal("0")

    presentation = Presentation["Sale"](
        readonly=lambda s: (s.number,),
        readonly_when=lambda s: (
            When(Is(s.state, Operator.NE, SaleState.DRAFT), s.customer_id, s.amount),
        ),
    )


sales = registry()
sale_table = entity_table(
    "hinted_sale",
    sales.metadata,
    Column("customer_id", Text, nullable=False),
    Column("number", Text, nullable=False),
    Column(
        "state",
        Enum(
            SaleState,
            native_enum=False,
            values_callable=lambda kind: [one.value for one in kind],
        ),
        nullable=False,
    ),
    Column("amount", Numeric(12, 2), nullable=False),
)
map_aggregates(sales, {Sale: sale_table})

HINTS = ("readonly", "required", "default", "readonly_when", "required_when", "visible_when")


def test_the_mapped_class_publishes_the_hints_its_declaration_says():
    on_the_class = describe_class(Sale, "id").model_dump(mode="json")["fields"]
    on_the_table = describe(Sale).model_dump(mode="json")["fields"]

    for name in on_the_class:
        assert {hint: on_the_table[name][hint] for hint in HINTS} == {
            hint: on_the_class[name][hint] for hint in HINTS
        }, name
    assert on_the_table["customer_id"]["required"] is True
    assert on_the_table["number"]["readonly"] is True


class ResponseSale(ResponseRecord):
    sale: Sale


class QueryGetSale(Get[Sale, ResponseSale]):
    pass


def test_a_confirmed_sale_still_saves_a_new_customer_and_the_screen_is_told_to_grey_it():
    database = Database("sqlite://")
    sales.metadata.create_all(database.engine)
    store = Repository(database)
    with store.context() as repository:
        sale = Sale(customer_id="c1", number="V-1", state=SaleState.CONFIRMED)
        repository.save(sale)
    with store.context() as repository:
        stored = repository.get(Sale, sale.id)
        assert stored is not None
        stored.customer_id = "c2"
        repository.save(stored)

    bus = UseFramework("sales-screen", log_after_execution=False)
    bus.add_dependency("repository", store)

    @bus.feature(QueryGetSale)
    class SaleReads(EntityReads[Sale]):
        pass

    answer = bus(QueryGetSale(id=sale.id), ResponseSale).model_dump(mode="json")

    assert answer["sale"]["customer_id"] == "c2"
    assert answer["model_meta_data"]["fields"]["customer_id"]["readonly_when"] == {
        "field": "state",
        "operator": "!=",
        "value": "draft",
    }
