"""One consolidated `Updated` event per aggregate per flush, taken from the engine's own record
of what it is about to write.

    class Invoice(ChangeTrackingMixin, Entity):
        total: int = 0

    with repository.context() as unit:
        invoice = unit.get(Invoice, invoice_id)
        invoice.total = 900                      # no save() — the session already knows

    invoice.pull_events()
    # [EntityUpdated(changes={"total": (500, 900)})]

**Registered on the session, not on the repository, and that is the whole point.** A rule on a
repository runs for whoever goes through that repository. This runs for whoever writes through
this `Database` — a use case holding a plain session, a script, a migration. What the framework
*guarantees* has to live where nothing can step around it, which is the same reason `_stamping`
is registered there.

**The diff is the engine's, not ours.** SQLAlchemy keeps the loaded value and the pending one
for every column it is about to write, so `get_history` answers exactly what changed, for every
route that reaches the database — including an aggregate loaded inside `context()`, modified,
and flushed without anybody calling `save()`. A baseline kept on the aggregate could only see
the calls it was told about, and silently missed that one.

`before_flush` and not `after_flush`: both can still read the history, but only the first can
still change the record, so the framework's two participants — this and the stamping — share
one moment and one ordering.
"""

from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session, attributes

from sincpro_framework.ddd.entity import ChangeTrackingMixin


def changed_columns(record: ChangeTrackingMixin) -> dict[str, tuple[Any, Any]]:
    """`{field: (before, after)}` for the tracked columns this flush is about to write.

    Only what `tracked_fields()` allows, so the aggregate still decides what counts: its
    bookkeeping columns, a field marked `tracked=False` and anything pointing at another
    aggregate stay out, exactly as they do everywhere else.
    """
    tracked = record.tracked_fields()
    changes: dict[str, tuple[Any, Any]] = {}
    state = inspect(record)
    if state is None:  # not a mapped aggregate; nothing the engine knows about
        return changes
    for column in state.mapper.column_attrs:
        if column.key not in tracked:
            continue
        history = attributes.get_history(record, column.key)
        if history.has_changes():
            before = history.deleted[0] if history.deleted else None
            after = history.added[0] if history.added else None
            changes[column.key] = (before, after)
    return changes


def _tracking(session: Session) -> None:
    """Records one event per aggregate that is actually changing.

    `session.dirty` is anything SQLAlchemy is *considering*: an attribute set to the value it
    already held puts the object here without changing a column. `changed_columns` answers
    `{}` for those, and nothing is recorded — an event for a write that never happened is
    worse than no event.

    A first save is in `session.new`, never in `dirty`, so a create records nothing here. That
    is a Created fact, written by hand, and deliberately not this.
    """
    for record in session.dirty:
        if not isinstance(record, ChangeTrackingMixin):
            continue
        record.record_change(changed_columns(record))
