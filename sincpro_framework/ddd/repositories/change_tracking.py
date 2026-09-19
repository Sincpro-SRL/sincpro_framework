"""Change tracking for a store that has no flush to ask: compare against what was handed out.

    class MemoryRepository(ChangeTrackingRepositoryMixin, Repository): ...

**This is the approximation, and the SQLAlchemy store does not use it.** That one asks the
engine what it is about to write (`orm/sqlalchemy/change_tracking.py`), which is exact and
which sees every route to the database. A store with no engine underneath has only the calls it
was told about, so it takes a baseline when it hands an aggregate out and compares on `save()`.

What follows from that, and is worth knowing before writing a test against the double: a change
made to an aggregate that is never `save()`d records nothing here. On SQLAlchemy it records,
because the row changed. The double is honest about the calls it sees; it cannot be honest
about a flush it does not have.

A mixin and not an injected `Rule`, because this is not a decision of one deployment about one
aggregate: it is behaviour the framework ships, opt-in on the *entity* — an aggregate that
inherits `ChangeTrackingMixin` gets it with no repository configuration at all, and one that
does not pays nothing.
"""

from typing import Any

from sincpro_framework.ddd.entity import ChangeTrackingMixin


class ChangeTrackingRepositoryMixin:
    """Two of `Repository`'s hooks, overridden: the baseline on the way out, the consolidated
    event on the way in. Both are no-ops for any record that is not `ChangeTrackingMixin`.

    **Mixed in before `Repository` in the bases**, so these run instead of the empty ones; each
    calls `super()` so a third mixin further along the chain still gets its turn.
    """

    def after_read(self, record: Any) -> Any:
        """Sets the baseline the next `save()` compares against."""
        if isinstance(record, ChangeTrackingMixin):
            record._snapshot()
        return super().after_read(record)  # type: ignore[misc]

    def before_save(self, record: Any) -> None:
        """Consolidates the diff into one event and moves the baseline to the current state,
        so a second `save()` inside one unit of work starts clean."""
        if isinstance(record, ChangeTrackingMixin):
            record.record_changes()
        super().before_save(record)  # type: ignore[misc]
