"""The table of a bounded context's domain events — one table, every event of the context.

    events = event_table("billing_events", metadata)
    map_events(mapper, BillingDomainEvent, events)      every subclass of it, kept here

A domain event is an entity: its envelope is columns, what each class adds is `payload`.
`event_columns()` is the envelope alone, for an event mapped to a table of its own.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from sincpro_framework.orm.sqlalchemy.domain.custom_fields import JsonText


def event_table(name: str, metadata: MetaData) -> Table:
    """One row per domain event of the context, in the order they were committed.

        out     id TEXT PRIMARY KEY — a UUID v7: ordering by it is ordering by time
                name TEXT (the class, by its wire name)
                entity_type · entity_id · entity_version (its place in that entity's history)
                correlation_id · causation_id · label · created_at
                payload JSON — what the event's own class adds (JSONB on Postgres)
                is_deliverable · delivered_at · next_delivery_at — what the relay filters
                delivery JSON — where its delivery stands, for a person: attempts, last error
                UNIQUE (entity_type, entity_id, entity_version)    two writers of one entity: one wins
                INDEX (name, id) · INDEX (entity_type, entity_id, id)
                INDEX (next_delivery_at) WHERE the event is deliverable and not delivered yet

    The fact never changes after the insert; only the delivery columns of a deliverable event
    do, as the relay delivers it.
    """
    moment = DateTime(timezone=True)
    return Table(
        name,
        metadata,
        Column("id", Text, primary_key=True),
        Column("name", Text, nullable=False),
        Column("entity_type", Text, nullable=False, default=""),
        Column("entity_id", Text, nullable=False, default=""),
        Column("entity_version", Integer),
        Column("correlation_id", Text),
        Column("causation_id", Text),
        Column("label", JsonText),
        Column("created_at", moment, nullable=False),
        Column("payload", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        Column("is_deliverable", Boolean, nullable=False, default=False),
        Column("delivered_at", moment),
        Column("next_delivery_at", moment),
        Column("delivery", JSON().with_variant(JSONB(), "postgresql")),
        UniqueConstraint(
            "entity_type", "entity_id", "entity_version", name=f"{name}_entity_version"
        ),
        Index(f"{name}_by_name", "name", "id"),
        Index(f"{name}_by_entity", "entity_type", "entity_id", "id"),
        Index(
            f"{name}_to_deliver",
            "next_delivery_at",
            postgresql_where=text("is_deliverable AND delivered_at IS NULL"),
            sqlite_where=text("is_deliverable AND delivered_at IS NULL"),
        ),
    )


def event_columns() -> list[Column]:
    """The envelope every `DomainEvent` carries, for a table that stores one.

        out     label JSON · entity_type TEXT · entity_id TEXT
                correlation_id TEXT · causation_id TEXT · entity_version INTEGER

        entity_table("run_advanced", metadata, *event_columns(), Column("run_id", Text))

    **A `DomainEvent` is an `Entity`**, so the repository that already exists stores and queries
    one like any other aggregate. This is the envelope alone, for an event class mapped to a table
    of its own, its own fields beside it; every event of a context in one table is `event_table`.

    `label` is a text per locale and needs `JsonText`, which is the one a hand-written table
    gets wrong: declaring it `Text` fails at the insert with `type 'dict' is not supported`,
    naming a parameter number rather than the column.
    """
    return [
        Column("label", JsonText),
        Column("entity_type", Text),
        Column("entity_id", Text),
        Column("correlation_id", Text),
        Column("causation_id", Text),
        Column("entity_version", Integer),
    ]
