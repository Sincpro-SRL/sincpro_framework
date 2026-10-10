"""The column an `ArchivableMixin` aggregate adds."""

from sqlalchemy import Column, DateTime
from sqlalchemy.types import TypeEngine


def archive_columns(datetime_type: TypeEngine | None = None) -> list[Column]:
    """The column an `ArchivableMixin` aggregate adds.

        out     archived_at TIMESTAMP NULL

    Indexed by the project when the table is large: every reading that did not ask for the
    archived ones filters on it.
    """
    moment = datetime_type if datetime_type is not None else DateTime(timezone=True)
    return [Column("archived_at", moment)]
