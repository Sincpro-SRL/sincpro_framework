"""One `save` of aggregates of several types writes each before what references it — whatever
the classes are called. SQLAlchemy orders a flush by relationships the framework does not
declare, and without them by class name: `Address` before `Zone`, a child before its parent,
which Postgres refuses."""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Column, ForeignKey, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd import Entity
from sincpro_framework.orm import Database, Repository, entity_table, map_aggregates


@dataclass
class Zone(Entity):
    name: str = ""


@dataclass
class Address(Entity):
    zone_id: str = ""


MAPPING = registry()
ZONES = entity_table("flush_zone", MAPPING.metadata, Column("name", Text))
map_aggregates(
    MAPPING,
    {
        Zone: ZONES,
        Address: entity_table(
            "flush_address", MAPPING.metadata, Column("zone_id", Text, ForeignKey(ZONES.c.id))
        ),
    },
)


def test_a_parent_is_written_before_the_child_saved_beside_it(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'order.sqlite3'}", enforce_foreign_keys=True)
    MAPPING.metadata.create_all(database.engine)
    repository = Repository(database)
    zone = Zone(name="north")

    repository.save([Address(zone_id=zone.id), zone])

    assert repository.count(Address).value == 1 and repository.count(Zone).value == 1
