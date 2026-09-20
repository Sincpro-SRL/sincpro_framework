"""`billing` — what the customer owes.

Hears that stock was reserved and issues an invoice. **It does not hold the order**: when it
needs something the order knows, it asks — a command on the `orders` bus, answered by that
context. That is the reference that works whether or not the two share a database.

Its outbox is here too: the fact and the invoice commit together, and a relay is what sends it.
"""

from dataclasses import dataclass

from sqlalchemy import Column, Index, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd.criteria import Condition, Criteria, Sort
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.entity_collection import EntityCollection
from sincpro_framework.ddd.events import EventStatus, EventTrackableMixin
from sincpro_framework.orm.sqlalchemy.data_mapper import (
    delivery_columns,
    entity_table,
    event_columns,
    map_aggregates,
)
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .contracts import InvoiceIssued, StockReserved


@dataclass
class Invoice(Entity):
    order_id: str = ""
    customer: str = ""
    total: int = 0


@dataclass(kw_only=True)
class Outbox(EventTrackableMixin, InvoiceIssued):
    """The fact, with its delivery state, written in the same transaction as the invoice."""

    name = "billing.v1.invoice_issued"


class Invoices(EntityCollection[Invoice]):
    pass


class Outboxes(EntityCollection[Outbox]):
    pass


mapper = registry()
metadata = mapper.metadata

invoice_table = entity_table(
    "shop_invoice",
    metadata,
    Column("order_id", Text),
    Column("customer", Text),
    Column("total", Integer),
)
outbox_table = entity_table(
    "shop_billing_outbox",
    metadata,
    *event_columns(),
    *delivery_columns(),
    Column("order_id", Text),
    Column("invoice_id", Text),
    Column("total", Integer),
    Index("shop_billing_pending", "status"),
)
map_aggregates(mapper, {Invoice: invoice_table, Outbox: outbox_table})

PENDING = Criteria(
    where=Condition(field="status", value=EventStatus.PENDING.value),
    # Ordered, and it is not a detail: a relay that claims without an order delivers the facts
    # in whatever order the engine felt like, and anything that folds them ends up wrong.
    order=(Sort(field="id"),),
)


class ResponseHeard(DataTransferObject):
    heard: bool = True


class RefusedToInvoice(Exception):
    """What a step of a saga failing looks like: an ordinary exception, from one context."""


def build(repository: Repository, ask_orders, refuse: bool = False) -> UseFramework:
    """`ask_orders` is how this context reaches the other one: a callable that runs a command
    on the `orders` bus. It is handed in rather than imported, so `billing` never learns that
    `orders` exists as a module — only that somebody can answer the question."""
    bus = UseFramework("billing", log_after_execution=False)
    bus.add_dependency("repository", repository)
    bus.add_dependency("ask_orders", ask_orders)

    @bus.feature(StockReserved)
    class IssueInvoice(Feature):
        repository: Repository
        ask_orders: object

        def execute(self, dto: StockReserved) -> ResponseHeard:
            """1. Ask the other context for what only it knows.
            2. The invoice and the fact, in one transaction.
            3. Final: nothing is published here — the relay sends it."""
            if refuse:
                raise RefusedToInvoice(f"billing refused order {dto.order_id}")
            told = self.ask_orders(dto.order_id)  # type: ignore[operator]
            invoice = Invoice(order_id=dto.order_id, customer=told.customer, total=told.total)
            with self.repository.context() as unit:
                unit.save(invoice)
                unit.save(
                    Outbox(
                        order_id=dto.order_id,
                        invoice_id=invoice.id,
                        total=invoice.total,
                    )
                )
            return ResponseHeard()

    return bus


def relay(repository: Repository, publisher) -> int:
    """Claim what is pending, deliver it, acknowledge.

    1. Claim under a row lock, so two relays never take the same fact.
    2. Publish outside the transaction — a subscriber must not run inside our write.
    3. Final: acknowledge what went out.
    """
    with repository.context() as unit:
        claimed = list(unit.search(Outboxes, PENDING, for_update=True, skip_locked=True))
        for one in claimed:
            one.mark_processing()
            unit.save(one)

    for one in claimed:
        publisher.publish(
            InvoiceIssued(order_id=one.order_id, invoice_id=one.invoice_id, total=one.total)
        )

    with repository.context() as unit:
        for one in claimed:
            one.mark_acknowledged()
            unit.save(one)
    return len(claimed)
