"""`notifications` — what the customer was told, **event-sourced**.

The other three keep aggregates and a log beside them. This one keeps only facts: its state is
what they add up to. That is the difference this package exists to show — not two styles of
framework, two styles of *context*, on one vocabulary.

It writes down the same facts `orders` does, in its own table. **Neither is a copy**: `orders`
keeps them because an order's history is its question, this one because the timeline *is* its
state.
"""

from dataclasses import dataclass

from sqlalchemy import Column, Text
from sqlalchemy.orm import registry

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd.criteria import Condition, Criteria, Sort
from sincpro_framework.ddd.entity.entity_collection import EntityCollection
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.orm.sqlalchemy.data_mapper import (
    entity_table,
    event_columns,
    map_aggregates,
)
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .contracts import InvoiceIssued, OrderPlaced, StockRejected, StockReserved


@dataclass(kw_only=True)
class Told(DomainEvent):
    """One thing the customer was told. There is no row for "the notification state of an
    order" anywhere: that state is these facts, in the order they were written."""

    name = "notifications.v1.told"
    order_id: str = ""
    said: str = ""


class Tolds(EntityCollection[Told]):
    pass


mapper = registry()
metadata = mapper.metadata
told_table = entity_table(
    "shop_told", metadata, *event_columns(), Column("order_id", Text), Column("said", Text)
)
map_aggregates(mapper, {Told: told_table})


class ResponseHeard(DataTransferObject):
    heard: bool = True


@dataclass
class Timeline:
    """The state, rebuilt. Never stored — it is derived, and derived things are questions."""

    order_id: str
    status: str
    steps: int


def build(repository: Repository) -> UseFramework:
    bus = UseFramework("notifications", log_after_execution=False)
    bus.add_dependency("repository", repository)

    @bus.feature([OrderPlaced, StockReserved, StockRejected, InvoiceIssued])
    class WriteItDown(Feature):
        repository: Repository

        def execute(self, dto) -> ResponseHeard:
            self.repository.save(Told(order_id=dto.order_id, said=dto.name))
            return ResponseHeard()

    return bus


READS = {
    OrderPlaced.name: "placed",
    StockReserved.name: "reserved",
    StockRejected.name: "out of stock",
    InvoiceIssued.name: "invoiced",
}


def timeline_of(repository: Repository, order_id: str) -> Timeline:
    """The order as its facts add up to it, in the order they happened.

    Ordering by `id` is ordering by time: an id is a UUID v7.
    """
    facts = repository.fetch_all(
        Tolds,
        Criteria(
            where=Condition(field="order_id", value=order_id), order=(Sort(field="id"),)
        ),
    )
    status = "unknown"
    for fact in facts:
        status = READS.get(fact.said, status)
    return Timeline(order_id=order_id, status=status, steps=len(facts))
