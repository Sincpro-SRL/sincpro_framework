"""The columns an `AuditedMixin` aggregate adds."""

from sqlalchemy import Column, Text


def audit_columns() -> list[Column]:
    """The two columns an `AuditedMixin` aggregate adds.

        out     created_by TEXT · updated_by TEXT

        entity_table("invoice", metadata, *audit_columns(), Column("number", Text))

    Written by the adapter from the `Database`'s actor, never by a use case.
    """
    return [Column("created_by", Text), Column("updated_by", Text)]
