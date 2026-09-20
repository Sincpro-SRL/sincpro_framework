"""`orders` — what a customer asked for.

**Two aggregates that share a database.** An `Order` and its `OrderLine`s belong together
closely enough to live in one schema, so the key between them is a real foreign key the engine
enforces and the relation is inferred from the column. That is what a bounded context *is*.

It keeps its own log of every fact it heard, in its own table.
"""

from dataclasses import dataclass, field

from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import registry

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd.criteria import Condition, Criteria, Sort
from sincpro_framework.ddd.entity import ChangeTrackingMixin, Entity
from sincpro_framework.ddd.entity.entity_collection import EntityCollection
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.orm.sqlalchemy.data_mapper import (
    entity_table,
    event_columns,
    map_aggregates,
)
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .contracts import InvoiceIssued, OrderPlaced, StockRejected, StockReserved


@dataclass
class Order(ChangeTrackingMixin, Entity):
    """Tracked: a change to an order after it was placed is something somebody audits."""

    customer: str = ""
    state: str = "placed"
    total: int = 0
    lines: list["OrderLine"] = field(default_factory=list)

    def refused(self, why: str) -> None:
        if self.state != "placed":
            raise ContractViolation(f"an order that is {self.state} cannot be refused")
        self.state = f"rejected: {why}"


@dataclass
class OrderLine(Entity):
    """The other aggregate in this database. Its key to `Order` is a real foreign key."""

    order_id: str = ""
    sku: str = ""
    quantity: int = 0
    price: int = 0


@dataclass(kw_only=True)
class OrderEvent(DomainEvent):
    """Everything this context heard, kept here. A `DomainEvent` is an `Entity`, so this is a
    table and the repository — not a store of its own."""

    name = "orders.v1.event"
    order_id: str = ""
    said: str = ""
    detail: str = ""


class Orders(EntityCollection[Order]):
    pass


class OrderLines(EntityCollection[OrderLine]):
    pass


class OrderEvents(EntityCollection[OrderEvent]):
    pass


mapper = registry()
metadata = mapper.metadata

order_table = entity_table(
    "shop_order",
    metadata,
    Column("customer", Text),
    Column("state", Text),
    Column("total", Integer),
)
line_table = entity_table(
    "shop_order_line",
    metadata,
    # The foreign key inside one context: the engine enforces it, the relation is inferred.
    Column("order_id", String, ForeignKey("shop_order.id"), nullable=False),
    Column("sku", Text),
    Column("quantity", Integer),
    Column("price", Integer),
)
event_table = entity_table(
    "shop_order_event",
    metadata,
    *event_columns(),
    Column("order_id", Text),
    Column("said", Text),
    Column("detail", Text),
)
map_aggregates(mapper, {Order: order_table, OrderLine: line_table, OrderEvent: event_table})


class CommandPlaceOrder(DataTransferObject):
    customer: str = ""
    sku: str = ""
    quantity: int = 1
    price: int = 100


class ResponseOrder(DataTransferObject):
    order_id: str
    state: str


class CommandTellAboutOrder(DataTransferObject):
    """What another context asks when it needs something only this one knows."""

    order_id: str = ""


class ResponseAboutOrder(DataTransferObject):
    customer: str
    total: int
    state: str


class ResponseHeard(DataTransferObject):
    heard: bool = True


def build(repository: Repository, publisher) -> UseFramework:
    bus = UseFramework("orders", log_after_execution=False)
    bus.add_dependency("repository", repository)
    bus.add_dependency("publisher", publisher)

    @bus.feature(CommandPlaceOrder)
    class PlaceOrder(Feature):
        repository: Repository
        publisher: object

        def execute(self, dto: CommandPlaceOrder) -> ResponseOrder:
            """1. The order, its line and the fact, in one transaction.
            2. Final: publish once the block committed — never from inside it."""
            total = dto.quantity * dto.price
            order = Order(customer=dto.customer, total=total)
            with self.repository.context() as unit:
                unit.save(order)
                unit.save(
                    OrderLine(
                        order_id=order.id,
                        sku=dto.sku,
                        quantity=dto.quantity,
                        price=dto.price,
                    )
                )
                unit.save(
                    OrderEvent(order_id=order.id, said=OrderPlaced.name, detail=dto.sku)
                )
            self.publisher.publish(  # type: ignore[attr-defined]
                OrderPlaced(
                    order_id=order.id,
                    sku=dto.sku,
                    quantity=dto.quantity,
                    total=total,
                )
            )
            return ResponseOrder(order_id=order.id, state=order.state)

    @bus.feature(CommandTellAboutOrder)
    class TellAboutOrder(Feature):
        """**The reference that works across a database boundary.** Another context asks a
        question and this one answers it; nothing is joined, and nothing needs to be in the
        same schema. Inside one database a foreign key would do the same job with less, which
        is exactly the trade the shapes are about."""

        repository: Repository

        def execute(self, dto: CommandTellAboutOrder) -> ResponseAboutOrder:
            order = self.repository.get(Order, dto.order_id)
            if order is None:
                raise ContractViolation(f"no order {dto.order_id}")
            return ResponseAboutOrder(
                customer=order.customer, total=order.total, state=order.state
            )

    @bus.feature([StockReserved, StockRejected, InvoiceIssued])
    class WriteItDown(Feature):
        """One Feature for three facts. Registering several classes at once is the whole
        subscription — nothing else is declared anywhere."""

        repository: Repository

        def execute(self, dto) -> ResponseHeard:
            with self.repository.context() as unit:
                unit.save(
                    OrderEvent(
                        order_id=dto.order_id,
                        said=dto.name,
                        detail=getattr(dto, "sku", ""),
                    )
                )
                if isinstance(dto, StockRejected):
                    order = unit.get(Order, dto.order_id)
                    if order is not None:
                        order.refused("out of stock")
                        unit.save(order)
            return ResponseHeard()

    return bus


def history_of(repository: Repository, order_id: str) -> list[OrderEvent]:
    """What this context can answer on its own, without asking anybody.

    Ordered by `id`, which is ordered by time: an id is a UUID v7. A history read without an
    order is not a history — the engine may hand the rows back however it likes.
    """
    return list(
        repository.fetch_all(
            OrderEvents,
            Criteria(
                where=Condition(field="order_id", value=order_id),
                order=(Sort(field="id"),),
            ),
        )
    )
