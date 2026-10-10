"""The tables of an `Entity`: its four columns, and the table that starts with them."""

from typing import Any

from sqlalchemy import Column, DateTime, Integer, MetaData, Table, Text
from sqlalchemy.types import TypeEngine


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
