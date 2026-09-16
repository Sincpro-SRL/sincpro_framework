"""The Data Mapper: where an aggregate lives is declared once, here, and the aggregate never
learns it. A plain dataclass in `domain/`, a table here, one call that joins them; the Active
Record alternative, where the class knows its table, is the coupling this module exists to
avoid.

    metadata = MetaData()
    note_table = entity_table("note", metadata, Column("title", Text, nullable=False))
    map_aggregates(registry(), {Note: note_table})

Imperative mapping, so `domain/` stays plain dataclasses with no import from here. Two things
this saves every project from writing again: the four `Entity` columns per table, and the
guard against mapping a class twice — a context can be built more than once in one process,
and `map_imperatively` raises on the second time.

**An `Entity` is mapped with its `version` as SQLAlchemy's `version_id_col`.** That is what
turns `save` into a conditional update: the UPDATE carries `WHERE version = :loaded` and a
row that moved on answers zero rows, which the engine reports as `StaleAggregate`. The ORM
already had the machinery; this is the line that switches it on.
"""

from typing import Any

from sqlalchemy import Column, DateTime, Integer, MetaData, Table, Text
from sqlalchemy.orm import registry
from sqlalchemy.types import TypeEngine

from sincpro_framework.ddd.entity import Entity

# What an `Entity` column is called on disk, in the order the class declares them.
ENTITY_COLUMNS: tuple[str, ...] = ("id", "created_at", "updated_at", "version")


def entity_columns(datetime_type: TypeEngine | None = None) -> list[Column]:
    """The four columns every `Entity` table starts with.

        out     id TEXT PRIMARY KEY · created_at NOT NULL · updated_at · version INTEGER NOT NULL

    `datetime_type` is what a timestamp is stored as — a real `DateTime` by default, or a
    project's own decorator when its tables predate this layer and keep ISO text.
    """
    moment = datetime_type if datetime_type is not None else DateTime(timezone=True)
    return [
        Column("id", Text, primary_key=True),
        Column("created_at", moment, nullable=False),
        Column("updated_at", moment),
        Column("version", Integer, nullable=False, default=1),
    ]


def entity_table(
    name: str,
    metadata: MetaData,
    *columns: Any,
    datetime_type: TypeEngine | None = None,
) -> Table:
    """A table for an `Entity` subclass: the four base columns, then the aggregate's own.

        in      "note", metadata, Column("title", Text), Index("note_title", "title")
        out     Table("note", id, created_at, updated_at, version, title, + the index)

    Anything `Table` accepts after its columns — an `Index`, a constraint — passes through.
    """
    return Table(name, metadata, *entity_columns(datetime_type), *columns)


def map_aggregates(
    mapper_registry: registry,
    tables: dict[type, Table],
    properties: dict[type, dict[str, Any]] | None = None,
) -> None:
    """Maps each class to its table, once, switching on the version check for an `Entity`.

        in      registry, {Note: note_table, Dataset: dataset_table}
        out     both mapped; calling it again changes nothing

    `properties` is per class and is what `map_imperatively` takes: how to map an attribute
    onto a column with another name, say `{"id": dataset_table.c.dataset_id}` for a table
    that predates the `Entity` convention.

    """
    already = {mapper.class_ for mapper in mapper_registry.mappers}
    for entity, table in tables.items():
        if entity in already:
            continue

        options: dict[str, Any] = {}
        if properties and entity in properties:
            options["properties"] = properties[entity]
        if issubclass(entity, Entity) and "version" in table.c:
            options["version_id_col"] = table.c.version

        mapper_registry.map_imperatively(entity, table, **options)
