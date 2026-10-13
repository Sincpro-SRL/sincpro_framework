"""The slot an extensible entity reserves: light fields with no table and no migration of their own.

`docs/spec/extensibility.md` §3. The core reserves `properties` from its first release; a module
maps a key of it as a field, and the field is filtered, grouped and written out like a column.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Column, MetaData, Text, update
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm import map_aggregates, template_table
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.ddd import Entity
from sincpro_framework.ddd.criteria import Condition, Criteria, Grouping, Level, Measure

from .engines import fresh


def world(engine_url: str):
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class Invoice(Entity):
        number: str = ""

    table = entity_table(
        "invoice", md, template_table.properties_column(), Column("number", Text)
    )
    map_aggregates(
        reg,
        {Invoice: table},
        properties={
            Invoice: {
                "zone": template_table.slot_field(table, "zone"),
                "weight": template_table.slot_field(
                    table, "weight", template_table.SlotType.NUMERIC
                ),
            }
        },
    )
    database = fresh(engine_url, md)
    store = Repository(database)
    for number, zone, weight in (
        ("F-1", "Z1", "2.5"),
        ("F-2", "Z2", "4"),
        ("F-3", "Z1", "1"),
    ):
        record = Invoice(number=number)
        store.save(record)
        with database.engine.begin() as connection:
            connection.execute(
                update(table)
                .where(table.c.id == record.id)
                .values(properties={"zone": zone, "weight": float(weight)})
            )
    return Invoice, store


def test_a_key_of_the_slot_is_a_field_of_the_entity(engine_url):
    Invoice, _ = world(engine_url)

    fields = describe(Invoice).fields

    assert "zone" in fields and "weight" in fields and "properties" in fields


def test_a_key_of_the_slot_is_filtered_like_a_column(engine_url):
    Invoice, store = world(engine_url)

    found = store.search(Invoice, Criteria(where=Condition(field="zone", value="Z1")))

    assert sorted(one.number for one in found) == ["F-1", "F-3"]


def test_a_key_of_the_slot_is_grouped_and_measured_like_a_column(engine_url):
    Invoice, store = world(engine_url)

    buckets = store.group_by_levels(
        Invoice,
        Criteria(
            grouping=Grouping(
                group_by=(Level(field="zone"),),
                measures={"weight": Measure(function="sum", field="weight")},
            )
        ),
    )

    assert {bucket.value: Decimal(str(bucket.measures["weight"])) for bucket in buckets} == {
        "Z1": Decimal("3.5"),
        "Z2": Decimal("4"),
    }


def test_a_module_maps_a_key_of_the_slot_of_an_entity_it_does_not_own(engine_url):
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class Invoice(Entity):
        number: str = ""

    table = entity_table(
        "invoice", md, template_table.properties_column(), Column("number", Text)
    )
    map_aggregates(reg, {Invoice: table})  # the core, mapped alone
    assert "zone" not in describe(Invoice).fields

    map_aggregates(  # a module, later
        reg, {}, properties={Invoice: {"zone": template_table.slot_field(table, "zone")}}
    )

    assert "zone" in describe(Invoice).fields
    database = fresh(engine_url, md)
    store = Repository(database)
    record = Invoice(number="F-1")
    store.save(record)
    with database.engine.begin() as connection:
        connection.execute(
            update(table).where(table.c.id == record.id).values(properties={"zone": "Z1"})
        )
    found = store.search(Invoice, Criteria(where=Condition(field="zone", value="Z1")))
    assert [one.number for one in found] == ["F-1"]
