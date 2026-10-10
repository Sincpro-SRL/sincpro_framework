"""The table a bounded context keeps its stored use cases in — declared in its own metadata and
migrated with its other tables."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)


def use_case_table(metadata: MetaData, name: str = "runtime_use_case") -> Table:
    """The table of a context's stored use cases, on `metadata` — named for the context when
    several share a database: `use_case_table(metadata, "billing_use_case")`."""
    return Table(
        name,
        metadata,
        Column("name", String(200), primary_key=True),
        Column("position", Integer, nullable=False),
        Column("source", Text, nullable=False),
        Column("version", Integer, nullable=False),
        Column("active", Boolean, nullable=False),
        Column("replaces", String(400), nullable=True),
        Column("saved_at", DateTime(timezone=True), nullable=False),
    )
