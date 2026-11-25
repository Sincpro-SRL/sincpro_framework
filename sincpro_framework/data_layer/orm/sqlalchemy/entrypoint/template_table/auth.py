"""The tables auth keeps on a database — declared by the project, in its metadata and its
migrations, like every other table.

    key_value_table("auth_key_value", metadata)        DatabaseKeyValue: revocations, lockout,
                                                        one-time links, TOTP steps, leases
    api_key_table("api_key", metadata)                  DatabaseApiKeys
    grant_assignment_table("grant_assignment", metadata)
    entitlement_table("entitlement", metadata)          DatabaseGrants
    refresh_token_table("refresh_token", metadata)      DatabaseRefreshTokens

Context: no secret is ever a column — keys are digests, as the stores write them.
"""

from sqlalchemy import Boolean, Column, DateTime, Index, LargeBinary, MetaData, Table, Text


def key_value_table(name: str, metadata: MetaData) -> Table:
    """A key, its bytes and when they expire — `NULL` never.

    out     key TEXT PRIMARY KEY · value BLOB · expires_at TIMESTAMP (UTC) NULL
    """
    return Table(
        name,
        metadata,
        Column("key", Text, primary_key=True),
        Column("value", LargeBinary, nullable=False),
        Column("expires_at", DateTime, nullable=True),
    )


def api_key_table(name: str, metadata: MetaData) -> Table:
    """An API key by its digest, who it stands for and what it may do.

    out     digest TEXT PRIMARY KEY · subject · tenant NULL · permissions · scopes (JSON text)
            · revoked BOOLEAN · expires_at TIMESTAMP (UTC) NULL
    """
    return Table(
        name,
        metadata,
        Column("digest", Text, primary_key=True),
        Column("subject", Text, nullable=False),
        Column("tenant", Text, nullable=True),
        Column("permissions", Text, nullable=False),
        Column("scopes", Text, nullable=False),
        Column("revoked", Boolean, nullable=False),
        Column("expires_at", DateTime, nullable=True),
    )


def grant_assignment_table(name: str, metadata: MetaData) -> Table:
    """A role given to a subject in a tenant (`""` for none), until `valid_until` when temporary.

    out     subject · tenant · role (PRIMARY KEY together) · valid_until TIMESTAMP (UTC) NULL
    """
    return Table(
        name,
        metadata,
        Column("subject", Text, primary_key=True),
        Column("tenant", Text, primary_key=True),
        Column("role", Text, primary_key=True),
        Column("valid_until", DateTime, nullable=True),
        Index(f"ix_{name}_holders", "tenant", "role"),
    )


def entitlement_table(name: str, metadata: MetaData) -> Table:
    """What a tenant is entitled to.

    out     tenant · entitlement (PRIMARY KEY together)
    """
    return Table(
        name,
        metadata,
        Column("tenant", Text, primary_key=True),
        Column("entitlement", Text, primary_key=True),
    )


def refresh_token_table(name: str, metadata: MetaData) -> Table:
    """A refresh token by its digest — never the token — its session and when it was rotated.

    out     digest TEXT PRIMARY KEY · session · subject · record (JSON) · rotated_at NULL
            · session_ends TIMESTAMP (UTC) — its session's end; a session with no absolute end, its token's
    """
    return Table(
        name,
        metadata,
        Column("digest", Text, primary_key=True),
        Column("session", Text, nullable=False),
        Column("subject", Text, nullable=False),
        Column("record", Text, nullable=False),
        Column("rotated_at", DateTime, nullable=True),
        Column("session_ends", DateTime, nullable=False),
        Index(f"ix_{name}_subject", "subject"),
    )
