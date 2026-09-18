"""The aggregates the persistence tests are written against, and the factory that fills them.

**Throwaway models rather than a project's.** The claim under test is that the layer works
against *any* mapped class without being told anything about it — running it only against the
aggregate it was built alongside would not be evidence of that.

`Thing` is a plain dataclass with a nullable column and a list column, the two shapes the
translator has rules for. `Note` follows the `Entity` convention, so what the convention adds
— the version check, the stamped `updated_at`, the minted id, the translations — is tested
beside a class that does not. `Client` adds the two conventions an aggregate opts into,
`AuditedMixin` and `ArchivableMixin`.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, Integer, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd.entity import ArchivableMixin, AuditedMixin, Entity, Translated
from sincpro_framework.ddd.entity_collection import EntityCollection
from sincpro_framework.orm.sqlalchemy.custom_fields import JsonText
from sincpro_framework.orm.sqlalchemy.data_mapper import (
    archive_columns,
    audit_columns,
    entity_table,
    map_aggregates,
)

EPOCH = datetime(2026, 1, 1, 12, 0, 0)

ROW_COUNT = 25


@dataclass
class Thing:
    thing_id: str
    name: str
    size: int
    tags: list[str]
    made_at: datetime
    owner: str | None = None


class Things(EntityCollection[Thing]):
    """A subclass with no methods, to pin that the store hands back the type it was given."""


@dataclass
class Note(Entity):
    """The aggregate that follows the `Entity` convention, beside one that does not.

    `Thing` proves the layer works for any mapped class; this one proves what the convention
    adds — the version check, the stamped `updated_at`, the minted id, the labels.
    """

    title: str
    body: str = ""

    @classmethod
    def translations(cls) -> Translated:
        return {
            "name": {"default": "Note", "es": "Nota"},
            "labels": {
                "title": {"default": "Title", "es": "Título"},
                "body": {"default": "Body", "es": "Cuerpo"},
            },
        }


class Notes(EntityCollection[Note]):
    pass


@dataclass
class Client(AuditedMixin, ArchivableMixin, Entity):
    """The two conventions an aggregate opts into, on one class: who wrote it, and putting it
    away instead of deleting it."""

    name: str
    city: str = ""


class Clients(EntityCollection[Client]):
    pass


mapper_registry = registry()

thing_table = Table(
    "thing",
    mapper_registry.metadata,
    Column("thing_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("size", Integer, nullable=False),
    # Nullable on purpose: a list column written before the field existed holds NULL.
    Column("tags", JsonText),
    Column("made_at", DateTime, nullable=False),
    # Nullable on purpose: this is the column the ordering refusal is tested against.
    Column("owner", Text),
)

note_table = entity_table(
    "note",
    mapper_registry.metadata,
    Column("title", Text, nullable=False),
    Column("body", Text, nullable=False),
)

client_table = entity_table(
    "client",
    mapper_registry.metadata,
    *audit_columns(),
    *archive_columns(),
    Column("name", Text, nullable=False),
    Column("city", Text, nullable=False),
)

map_aggregates(mapper_registry, {Thing: thing_table, Note: note_table, Client: client_table})


def a_thing(number: int) -> Thing:
    """Deliberately not unique in `size`, so the keyset has to use its tiebreaker."""
    return Thing(
        thing_id=f"th_{number:04d}",
        name=f"thing {number}",
        size=number % 5,
        tags=["even" if number % 2 == 0 else "odd", f"n{number}"],
        made_at=EPOCH + timedelta(minutes=number),
        owner=None if number % 3 == 0 else f"owner-{number % 3}",
    )
