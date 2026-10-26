"""The records a write is run once by."""

from sqlalchemy import Column, DateTime, LargeBinary, MetaData, Table, Text


def idempotency_table(name: str, metadata: MetaData) -> Table:
    """The records `DatabaseIdempotencyRecords` stores: one row per idempotency key, its claim
    while the write runs, then its answer, completed in the write's own transaction.

        out     key TEXT PRIMARY KEY · state TEXT · fingerprint TEXT · owner TEXT ·
                answer BLOB · expires_at TIMESTAMP (UTC)

    Declared by the project, in its metadata and its migrations, like every other table.
    """
    return Table(
        name,
        metadata,
        Column("key", Text, primary_key=True),
        Column("state", Text, nullable=False),
        Column("fingerprint", Text, nullable=False),
        Column("owner", Text, nullable=False),
        Column("answer", LargeBinary, nullable=False),
        Column("expires_at", DateTime, nullable=False),
    )
