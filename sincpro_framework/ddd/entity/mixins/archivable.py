"""Put away rather than deleted."""

from dataclasses import dataclass
from datetime import datetime

from sincpro_framework.ddd.entity.entity import utc_now


@dataclass(kw_only=True)
class ArchivableMixin:
    """Put away rather than deleted: a mixin an aggregate opts into.

        @dataclass
        class Account(ArchivableMixin, Entity):
            code: str

        repository.archive(account)         →  archived_at stamped, the row stays
        repository.remove(account)          →  DELETE, when it really has to go
        repository.search(Accounts)         →  the live ones
        Criteria(where=Condition(field="archived_at", operator=Operator.IS_NULL, value=False))
                                            →  the archived ones, asked for by name

    **A read leaves the archived out unless the criteria names `archived_at`.** What a business
    calls deleting is almost always this: the record has to stop appearing and cannot be lost,
    because invoices point at it. Odoo spells it `active`; the column here says when.

    The column goes on the table with `archive_columns()`.
    """

    archived_at: datetime | None = None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def archive(self) -> None:
        """Puts the record away. Already archived, nothing moves: the moment it left is the
        first one, not the last time somebody asked again."""
        if self.archived_at is None:
            self.archived_at = utc_now()

    def restore(self) -> None:
        self.archived_at = None
