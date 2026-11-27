"""The table of a bounded context's external keys — the name another system gives each record.

    keys = external_key_table("sales_external_keys", metadata)
    map_aggregates(mapper, {ExternalKey: keys})

One table holds those of every entity of the context, as one table holds its events: an import
reads them to find the record a file names, and saves them in the same unit of work as the
records they name.
"""

from sqlalchemy import Column, Index, MetaData, Table, Text, UniqueConstraint

from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.entity import (
    entity_table,
)


def external_key_table(name: str, metadata: MetaData) -> Table:
    """One row per name an origin gives a record.

        out     the entity's columns (id, created_at, updated_at, version)
                origin TEXT · entity_type TEXT · entity_id TEXT · key TEXT
                scope TEXT NOT NULL DEFAULT ''          the partition a name lives in
                UNIQUE (scope, origin, entity_type, key)       one name, one record
                INDEX (entity_type, entity_id)          the names of one record

    Declared by the project, in its metadata and its migrations, like every other table.
    """
    return entity_table(
        name,
        metadata,
        Column("origin", Text, nullable=False),
        Column("entity_type", Text, nullable=False),
        Column("entity_id", Text, nullable=False),
        Column("key", Text, nullable=False),
        Column("scope", Text, nullable=False, default="", server_default=""),
        UniqueConstraint("scope", "origin", "entity_type", "key", name=f"{name}_named_once"),
        Index(f"{name}_of_record", "entity_type", "entity_id"),
    )
