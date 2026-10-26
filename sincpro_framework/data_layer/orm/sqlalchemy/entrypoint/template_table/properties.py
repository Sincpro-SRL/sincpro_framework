"""The slot an extensible entity reserves for light fields, and the fields read out of it."""

from enum import StrEnum

from sqlalchemy import JSON, Column, Table
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import ColumnProperty, column_property


class SlotType(StrEnum):
    """How a key of the slot is read: the type of the field it becomes."""

    TEXT = "text"
    INTEGER = "integer"
    NUMERIC = "numeric"
    BOOLEAN = "boolean"


def properties_column() -> Column:
    """The column an extensible entity reserves from its first release.

        out     properties JSON NULL        JSONB on PostgreSQL

        entity_table("invoice", metadata, properties_column(), Column("number", Text))

    A module that needs a field and not a table of its own stores it here, and maps it as a field
    with `slot_field`. Adding the column later is a migration of the whole table, which is why the
    core reserves it before anyone needs it.
    """
    return Column("properties", JSON().with_variant(JSONB(), "postgresql"))


def slot_field(table: Table, key: str, as_type: SlotType = SlotType.TEXT) -> ColumnProperty:
    """A key of the slot, mapped as a field of the entity.

        map_aggregates(registry, {Invoice: invoice_t},
                       properties={Invoice: {"zone": slot_field(invoice_t, "zone")}})

    The field is filtered, grouped and measured like a column; a record without the key holds
    NULL there. Keys are read as text unless `as_type` says another type. Every row carrying
    many keys costs space, since the names repeat in each one: a field every record fills belongs
    in a column of a table of its own.
    """
    value = table.c.properties[key]
    read = {
        SlotType.TEXT: value.as_string,
        SlotType.INTEGER: value.as_integer,
        SlotType.NUMERIC: value.as_numeric,
        SlotType.BOOLEAN: value.as_boolean,
    }[as_type]
    return column_property(read(*((18, 4) if as_type is SlotType.NUMERIC else ())))
