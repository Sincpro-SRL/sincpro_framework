"""What the context raises: an entry of its store kept over a version that moved."""

from sincpro_framework.exceptions import ClientError, FailureKind, ProgrammingError


class EntryConflict(ClientError):
    """A keep named a version that is no longer the entry's: another writer — a tab, a device, a
    replica — kept in between, or the entry expired. Read again, and keep with the version read.

    `current` is the version there now — `0` when there is no live entry.
    """

    failure_kind = FailureKind.CONFLICT

    def __init__(self, key: str, expected: int, current: int) -> None:
        super().__init__(
            f"{key} is at version {current}, not {expected}: read it again before keeping"
        )
        self.key = key
        self.expected = expected
        self.current = current


class DraftConflict(EntryConflict):
    """A keep named a version that is no longer the draft's: another tab, another device, kept
    in between, or the draft expired. The caller read before someone else wrote: read again,
    merge or reload, and keep with the version read.

    `key` is the draft's subject; `current` is the version there now — `0` when there is no
    live draft.
    """
