"""What the context raises: a draft kept over a version that moved — an `EntryConflict` of the
store, said for drafts."""

from sincpro_framework.exceptions import ClientError, FailureKind, ProgrammingError
from sincpro_framework.store.domain.exceptions import EntryConflict


class DraftConflict(EntryConflict):
    """A keep named a version that is no longer the draft's: another tab, another device, kept
    in between, or the draft expired. The caller read before someone else wrote: read again,
    merge or reload, and keep with the version read.

    `key` is the draft's subject; `current` is the version there now — `0` when there is no
    live draft.
    """
