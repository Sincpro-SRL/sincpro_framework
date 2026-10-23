"""Drafts: what a form keeps between requests before anything is saved — a context kept by whose
it is, guarded by its version.

    drafts = use_context().drafts()                       this tenant's, this user's
    draft = drafts.keep("invoice:01a1", values, origin_version=invoice.version, expected=0)
    draft = drafts.read("invoice:01a1")                   the latest, or None
    drafts.keep("invoice:01a1", newer, invoice.version, expected=draft.version)
                                                          DraftConflict if another tab kept in between
    drafts.discard("invoice:01a1")

Context: optional, and never a lock. Each keep names the version it read — `0` when there is no live
draft — and loses with `DraftConflict` when another keep came first, so two tabs never overwrite
each other in silence. Every draft has a TTL, renewed on each keep, so an abandoned one disappears
on its own. A version is an opaque number that never repeats for a key — not after a discard, not
after an expiry — so a tab still holding an old one cannot win.

The draft is not the record. Activating it is the project's Command: read the stored record,
`refuse_stale(draft, record)` (`sincpro_framework.ddd`) — a record changed since the draft started
is `StaleAggregate` — then `assign(record, draft.values)`, save it and discard the draft.
"""

from datetime import datetime
from typing import Any

from sincpro_framework.sincpro_abstractions import DataTransferObject


class Draft(DataTransferObject):
    """One kept draft: the form's values, as JSON, and the versions that guard them."""

    key: str
    values: dict[str, Any]
    """The values as JSON carries them — `"50.00"` for a decimal, ISO text for a date. Read
    back onto a record with the field types, as any value from a form."""
    version: int
    """This draft's own version: what the next keep names as `expected`. Opaque, like an
    ETag: it never repeats for the key, and it is not a count of keeps."""
    origin_version: int
    """The stored record's version when the draft started (`0` for a new record): what
    `refuse_stale` compares with the record read at activation."""
    kept_at: datetime
