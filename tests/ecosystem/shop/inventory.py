"""`inventory` — what is on the shelf.

Hears that an order was placed, and answers with a fact of its own: reserved, or rejected. Keeps
its own log, in its own table, because "what happened to this SKU" is its question and nobody
else's.
"""

from dataclasses import dataclass

from sqlalchemy import Column, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd.criteria import Condition, Criteria, Sort
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.entity_collection import EntityCollection
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.orm.sqlalchemy.data_mapper import (
    entity_table,
    event_columns,
    map_aggregates,
)
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .contracts import OrderPlaced, StockRejected, StockReserved


@dataclass
class StockItem(Entity):
    sku: str = ""
    on_hand: int = 0

    def enough_for(self, quantity: int) -> bool:
        return self.on_hand >= quantity


@dataclass(kw_only=True)
class InventoryEvent(DomainEvent):
    name = "inventory.v1.event"
    order_id: str = ""
    sku: str = ""
    said: str = ""


class StockItems(EntityCollection[StockItem]):
    pass


class InventoryEvents(EntityCollection[InventoryEvent]):
    pass


mapper = registry()
metadata = mapper.metadata

stock_table = entity_table(
    "shop_stock", metadata, Column("sku", Text), Column("on_hand", Integer)
)
event_table = entity_table(
    "shop_inventory_event",
    metadata,
    *event_columns(),
    Column("order_id", Text),
    Column("sku", Text),
    Column("said", Text),
)
map_aggregates(mapper, {StockItem: stock_table, InventoryEvent: event_table})


class CommandStock(DataTransferObject):
    sku: str = ""
    on_hand: int = 0


class ResponseStock(DataTransferObject):
    sku: str
    on_hand: int


class CommandReleaseStock(DataTransferObject):
    """The compensation. A saga has no transaction across contexts, so undoing a step is
    another step — an ordinary command, with its own fact."""

    order_id: str = ""
    sku: str = ""
    quantity: int = 0


class ResponseHeard(DataTransferObject):
    heard: bool = True


def build(repository: Repository, publisher) -> UseFramework:
    bus = UseFramework("inventory", log_after_execution=False)
    bus.add_dependency("repository", repository)
    bus.add_dependency("publisher", publisher)

    @bus.feature(CommandStock)
    class PutOnShelf(Feature):
        repository: Repository

        def execute(self, dto: CommandStock) -> ResponseStock:
            item = StockItem(sku=dto.sku, on_hand=dto.on_hand)
            self.repository.save(item)
            return ResponseStock(sku=item.sku, on_hand=item.on_hand)

    @bus.feature(CommandReleaseStock)
    class ReleaseStock(Feature):
        """Puts back what a later step could not use. Not a rollback — the reservation really
        happened, and so does this."""

        repository: Repository

        def execute(self, dto: CommandReleaseStock) -> ResponseHeard:
            with self.repository.context() as unit:
                item = unit.search(
                    StockItems, Criteria(where=Condition(field="sku", value=dto.sku))
                ).first()
                if item is not None:
                    item.on_hand += dto.quantity
                    unit.save(item)
                unit.save(
                    InventoryEvent(
                        order_id=dto.order_id, sku=dto.sku, said="inventory.v1.released"
                    )
                )
            return ResponseHeard()

    @bus.feature(OrderPlaced)
    class ReserveStock(Feature):
        """1. Find the SKU and decide, inside one transaction.
        2. Write what was decided, and the fact of deciding it.
        3. Final: say it, once the block committed."""

        repository: Repository
        publisher: object

        def execute(self, dto: OrderPlaced) -> ResponseHeard:
            with self.repository.context() as unit:
                item = unit.search(
                    StockItems,
                    Criteria(where=Condition(field="sku", value=dto.sku)),
                ).first()
                enough = item is not None and item.enough_for(dto.quantity)
                if enough and item is not None:
                    item.on_hand -= dto.quantity
                    unit.save(item)
                said = StockReserved.name if enough else StockRejected.name
                unit.save(InventoryEvent(order_id=dto.order_id, sku=dto.sku, said=said))
                left = item.on_hand if item is not None else 0

            if enough:
                self.publisher.publish(  # type: ignore[attr-defined]
                    StockReserved(order_id=dto.order_id, sku=dto.sku, quantity=dto.quantity)
                )
            else:
                self.publisher.publish(  # type: ignore[attr-defined]
                    StockRejected(
                        order_id=dto.order_id,
                        sku=dto.sku,
                        wanted=dto.quantity,
                        left=left,
                    )
                )
            return ResponseHeard()

    return bus


def history_of(repository: Repository, order_id: str) -> list[InventoryEvent]:
    return list(
        repository.fetch_all(
            InventoryEvents,
            Criteria(
                where=Condition(field="order_id", value=order_id),
                order=(Sort(field="id"),),
            ),
        )
    )


def on_hand(repository: Repository, sku: str) -> int:
    item = repository.search(
        StockItems, Criteria(where=Condition(field="sku", value=sku))
    ).first()
    return item.on_hand if item is not None else 0
