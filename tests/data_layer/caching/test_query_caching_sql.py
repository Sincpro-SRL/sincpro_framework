"""On a real database: a Command's commit lets go of the answers that read what it wrote — the
repository noted the reads, the commit names the writes, and neither use case knows."""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import Column, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.common.store import InMemoryKeyValue
from sincpro_framework.data_layer.caching import CachePolicy, QueryCaching
from sincpro_framework.data_layer.orm import (
    Database,
    Repository,
    invalidate_on_commit,
    map_aggregates,
    template_table,
)
from sincpro_framework.ddd import Entity


@dataclass
class Order(Entity):
    total: int = 0


@dataclass
class Shipment(Entity):
    carrier: str = ""


MAPPING = registry()
map_aggregates(
    MAPPING,
    {
        Order: template_table.entity_table(
            "qc_order", MAPPING.metadata, Column("total", Integer)
        ),
        Shipment: template_table.entity_table(
            "qc_shipment", MAPPING.metadata, Column("carrier", Text)
        ),
    },
)


class QueryOrderCount(DataTransferObject):
    pass


class ResponseOrderCount(DataTransferObject):
    orders: int


class CommandPlaceOrder(DataTransferObject):
    order_id: str
    total: int


class CommandShip(DataTransferObject):
    shipment_id: str


def _shop(tmp_path: Path) -> tuple[UseFramework, list[str]]:
    database = Database(f"sqlite:///{tmp_path / 'shop.sqlite3'}")
    MAPPING.metadata.create_all(database.engine)
    shop = UseFramework("shop-cache", log_after_execution=False)
    shop.add_dependency("repository", Repository(database))
    counted: list[str] = []
    shop.add_dependency("counted", counted)

    @shop.feature(QueryOrderCount)
    class OrderCount(Feature):
        def execute(self, dto: QueryOrderCount) -> ResponseOrderCount:
            self.counted.append("count")
            return ResponseOrderCount(orders=self.repository.count(Order).value)

    @shop.feature(CommandPlaceOrder)
    class PlaceOrder(Feature):
        def execute(self, dto: CommandPlaceOrder) -> None:
            self.repository.save(Order(id=dto.order_id, total=dto.total))

    @shop.feature(CommandShip)
    class Ship(Feature):
        def execute(self, dto: CommandShip) -> None:
            self.repository.save(Shipment(id=dto.shipment_id, carrier="dhl"))

    caching = QueryCaching(InMemoryKeyValue())
    caching.on(shop, QueryOrderCount, CachePolicy(ttl=timedelta(minutes=5)))
    invalidate_on_commit(database, caching)
    return shop, counted


def test_a_commit_that_wrote_what_the_answer_counted_lets_it_go(tmp_path: Path):
    shop, counted = _shop(tmp_path)
    assert shop(QueryOrderCount(), ResponseOrderCount).orders == 0

    shop(CommandPlaceOrder(order_id="o1", total=10))

    assert shop(QueryOrderCount(), ResponseOrderCount).orders == 1
    assert counted == ["count", "count"]


def test_a_commit_that_wrote_something_else_keeps_it(tmp_path: Path):
    shop, counted = _shop(tmp_path)
    shop(QueryOrderCount(), ResponseOrderCount)

    shop(CommandShip(shipment_id="s1"))
    shop(QueryOrderCount(), ResponseOrderCount)

    assert counted == ["count"]
