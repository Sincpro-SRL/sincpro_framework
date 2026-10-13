"""One flat record of several entities: `Consolidated(Move, MoveDelivery, MoveFiscal)`.

Two extensions of the same entity are siblings: they cannot be one class by inheritance, and a
module that knows both can still read and write them as one. `docs/spec/extensibility.md` §5.
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, Numeric, Table, Text, text
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm import Consolidated, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.ddd import Entity
from sincpro_framework.ddd.criteria import Condition, Criteria
from sincpro_framework.exceptions import ProgrammingError

from .engines import fresh


def world(engine_url: str, fiscal_columns: tuple[Column, ...] = ()):
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class Move(Entity):
        number: str = ""
        total: Decimal = Decimal("0")

    @dataclass
    class MoveDelivery(Move):
        route: str = ""

    @dataclass
    class MoveFiscal(Move):
        cufd: str = ""

    @dataclass
    class MoveConsolidated(Entity):
        number: str = ""
        total: Decimal = Decimal("0")
        route: str = ""
        cufd: str = ""

    move_t = entity_table("move", md, Column("number", Text), Column("total", Numeric(12, 2)))
    delivery_t = Table(
        "move_delivery",
        md,
        Column("id", Text, ForeignKey("move.id"), primary_key=True),
        Column("route", Text),
        Column("zone", Text),
    )
    fiscal_t = Table(
        "move_fiscal",
        md,
        Column("id", Text, ForeignKey("move.id"), primary_key=True),
        Column("cufd", Text),
        *fiscal_columns,
    )
    map_aggregates(reg, {Move: move_t})
    map_aggregates(reg, {MoveDelivery: delivery_t})
    map_aggregates(reg, {MoveFiscal: fiscal_t})
    return reg, md, (Move, MoveDelivery, MoveFiscal, MoveConsolidated)


def test_sibling_extensions_are_one_flat_record_in_a_module_that_knows_them_both(engine_url):
    reg, md, (Move, MoveDelivery, MoveFiscal, MoveConsolidated) = world(engine_url)
    map_aggregates(reg, {MoveConsolidated: Consolidated(Move, MoveDelivery, MoveFiscal)})
    store = Repository(fresh(engine_url, md))

    store.save(
        MoveConsolidated(id="M1", number="F-1", total=Decimal("5"), route="R7", cufd="C1")
    )
    read = store.get(MoveConsolidated, "M1")

    assert read is not None
    assert (read.number, read.total, read.route, read.cufd) == (
        "F-1",
        Decimal("5"),
        "R7",
        "C1",
    )


def test_a_save_of_the_consolidated_record_writes_one_row_in_each_table(engine_url):
    reg, md, (Move, MoveDelivery, MoveFiscal, MoveConsolidated) = world(engine_url)
    map_aggregates(reg, {MoveConsolidated: Consolidated(Move, MoveDelivery, MoveFiscal)})
    database = fresh(engine_url, md)
    store = Repository(database)

    store.save(MoveConsolidated(id="M1", number="F-1", route="R7", cufd="C1"))

    with database.engine.connect() as connection:
        counts = [
            connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            for table in ("move", "move_delivery", "move_fiscal")
        ]
        route = connection.execute(text("SELECT route FROM move_delivery")).scalar()
    assert counts == [1, 1, 1] and route == "R7"


def test_editing_the_consolidated_record_updates_each_table_where_the_field_lives(engine_url):
    reg, md, (Move, MoveDelivery, MoveFiscal, MoveConsolidated) = world(engine_url)
    map_aggregates(reg, {MoveConsolidated: Consolidated(Move, MoveDelivery, MoveFiscal)})
    database = fresh(engine_url, md)
    store = Repository(database)
    store.save(MoveConsolidated(id="M1", number="F-1", route="R7", cufd="C1"))

    one = store.get(MoveConsolidated, "M1")
    assert one is not None
    one.route, one.cufd = "R9", "C2"
    store.save(one)

    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT route FROM move_delivery")).scalar() == "R9"
        assert connection.execute(text("SELECT cufd FROM move_fiscal")).scalar() == "C2"


def test_a_record_without_the_row_of_an_extension_reads_its_fields_as_empty(engine_url):
    reg, md, (Move, MoveDelivery, MoveFiscal, MoveConsolidated) = world(engine_url)
    map_aggregates(reg, {MoveConsolidated: Consolidated(Move, MoveDelivery, MoveFiscal)})
    database = fresh(engine_url, md)
    store = Repository(database)
    store.save(Move(id="A", number="F-A"))

    read = store.get(MoveConsolidated, "A")

    assert read is not None and (read.number, read.route, read.cufd) == ("F-A", "", "")


def test_the_consolidated_record_is_filtered_by_a_field_of_any_of_them(engine_url):
    reg, md, (Move, MoveDelivery, MoveFiscal, MoveConsolidated) = world(engine_url)
    map_aggregates(reg, {MoveConsolidated: Consolidated(Move, MoveDelivery, MoveFiscal)})
    store = Repository(fresh(engine_url, md))
    store.save(MoveConsolidated(id="M1", number="F-1", route="R7", cufd="C1"))
    store.save(MoveConsolidated(id="M2", number="F-2", route="R8", cufd="C2"))

    found = store.search(
        MoveConsolidated, Criteria(where=Condition(field="cufd", value="C2"))
    )

    assert [one.number for one in found] == ["F-2"]


def test_two_extensions_that_name_a_field_alike_are_refused_naming_both(engine_url):
    reg, md, (Move, MoveDelivery, MoveFiscal, _) = world(
        engine_url, fiscal_columns=(Column("route", Text),)
    )

    @dataclass
    class Clashing(Entity):
        route: str = ""

    with pytest.raises(ProgrammingError, match="move_delivery, move_fiscal"):
        map_aggregates(reg, {Clashing: Consolidated(Move, MoveDelivery, MoveFiscal)})


def test_a_report_over_the_consolidated_record_groups_by_a_field_of_an_extension_in_a_join(
    engine_url,
):
    from sincpro_framework.ddd.criteria import Grouping, Level, Measure

    reg, md, (Move, MoveDelivery, MoveFiscal, MoveConsolidated) = world(engine_url)
    map_aggregates(reg, {MoveConsolidated: Consolidated(Move, MoveDelivery, MoveFiscal)})
    store = Repository(fresh(engine_url, md))
    store.save(
        MoveConsolidated(id="M1", number="F-1", total=Decimal("2"), route="R7", cufd="C1")
    )
    store.save(
        MoveConsolidated(id="M2", number="F-2", total=Decimal("3"), route="R7", cufd="C2")
    )
    store.save(
        MoveConsolidated(id="M3", number="F-3", total=Decimal("4"), route="R9", cufd="C3")
    )
    criteria = Criteria(
        grouping=Grouping(
            group_by=(Level(field="route"),),
            measures={"total": Measure(function="sum", field="total")},
        )
    )

    buckets = store.group_by_levels(MoveConsolidated, criteria)

    assert {bucket.value: bucket.measures["total"] for bucket in buckets} == {
        "R7": Decimal("5"),
        "R9": Decimal("4"),
    }
    statement = str(store.statement(MoveConsolidated, Criteria()).compile())
    assert "LEFT OUTER JOIN" in statement and "SELECT route FROM" not in statement


def test_a_consolidated_record_created_in_a_core_that_stores_the_type_is_typed_by_its_rows(
    engine_url,
):
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class Move(Entity):
        number: str = ""

    @dataclass
    class MoveDelivery(Move):
        route: str = ""

    @dataclass
    class MoveAll(Entity):
        number: str = ""
        route: str = ""

    move_t = entity_table("move", md, Column("number", Text), Column("extension_type", Text))
    delivery_t = Table(
        "move_delivery",
        md,
        Column("id", Text, ForeignKey("move.id"), primary_key=True),
        Column("route", Text),
    )
    map_aggregates(reg, {Move: move_t})
    map_aggregates(reg, {MoveDelivery: delivery_t})
    map_aggregates(reg, {MoveAll: Consolidated(Move, MoveDelivery)})
    store = Repository(fresh(engine_url, md))

    store.save(MoveAll(id="M1", number="F-1", route="R7"))

    read = store.get(Move, "M1")
    assert type(read).__name__ == "MoveDelivery" and read.route == "R7"  # type: ignore[union-attr]
