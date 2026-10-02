"""`billing` — what the customer owes.

Hears that stock was reserved and issues an invoice. **It does not hold the order**: when it
needs something the order knows, it asks — a command on the `orders` bus, answered by that
context. That is the reference that works whether or not the two share a database.

Its events are kept here too: the invoice and the fact it was issued commit together, in
billing's event table, and the relay is what delivers the fact — the transactional outbox.
"""

from dataclasses import dataclass

from sqlalchemy import Column, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd import Criteria, DeliverableEventMixin, DomainEvent
from sincpro_framework.ddd.criteria import Condition, Operator, parse_order
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.entity_collection import EntityCollection
from sincpro_framework.event_driven import DeliveryFailurePolicy, EventRelay, RelayPass
from sincpro_framework.orm import event_table, map_events
from sincpro_framework.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.orm.sqlalchemy.entrypoint.templates import entity_table
from sincpro_framework.orm.sqlalchemy.services.data_mapper import map_aggregates

from .contracts import InvoiceIssued, StockReserved


@dataclass
class Invoice(Entity):
    order_id: str = ""
    customer: str = ""
    total: int = 0


class Invoices(EntityCollection[Invoice]):
    pass


@dataclass(kw_only=True)
class BillingEvent(DomainEvent):
    """Every event of billing: its one event table."""

    name = "billing.v1.event"


@dataclass(kw_only=True)
class IssuedInvoice(DeliverableEventMixin, InvoiceIssued, BillingEvent):
    """The contract other contexts hear, kept in billing's table and delivered by the relay."""

    name = "billing.v1.invoice_issued"


mapper = registry()
metadata = mapper.metadata

invoice_table = entity_table(
    "shop_invoice",
    metadata,
    Column("order_id", Text),
    Column("customer", Text),
    Column("total", Integer),
)
events_table = event_table("shop_billing_events", metadata)
map_aggregates(mapper, {Invoice: invoice_table})
map_events(mapper, BillingEvent, events_table)


def facts(repository: Repository) -> list[DomainEvent]:
    """Every event billing kept, oldest first."""
    return list(repository.fetch_all(BillingEvent, Criteria(order=parse_order("id"))).items)


def pending(repository: Repository) -> int:
    """The deliverable events not delivered yet."""
    undelivered = Criteria(
        where=Condition(field="delivered_at", operator=Operator.IS_NULL, value=True)
    )
    return repository.count(IssuedInvoice, undelivered).value


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
            3. Final: nothing is published here — the relay delivers it."""
            if refuse:
                raise RefusedToInvoice(f"billing refused order {dto.order_id}")
            told = self.ask_orders(dto.order_id)  # type: ignore[operator]
            invoice = Invoice(order_id=dto.order_id, customer=told.customer, total=told.total)
            issued = IssuedInvoice(
                order_id=dto.order_id, invoice_id=invoice.id, total=invoice.total
            )
            with self.repository.context() as unit:
                unit.save(invoice)
                unit.save([issued])
            return ResponseHeard()

    return bus


def relay(
    repository: Repository, publisher, on_failure: DeliveryFailurePolicy | None = None
) -> RelayPass:
    """One pass of billing's relay."""
    if on_failure is None:
        return EventRelay(repository, BillingEvent, publisher).run_once()
    return EventRelay(repository, BillingEvent, publisher, on_failure).run_once()
