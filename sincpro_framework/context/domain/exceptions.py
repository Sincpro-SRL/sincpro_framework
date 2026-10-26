"""What the context raises: an entry of the context store saved over a version that moved, and the
same said for drafts."""

from sincpro_framework.exceptions import (
    ClientError,
    ContextRequired,
    FailureKind,
    ProgrammingError,
)


class EntryConflict(ClientError):
    """A save named a version that is no longer the entry's: another writer — a tab, a device, a
    replica — saved in between, or the entry expired. Read again, and save with the version read.

    `current` is the version there now — `0` when there is no live entry.
    """

    failure_kind = FailureKind.CONFLICT

    def __init__(self, key: str, expected: int, current: int) -> None:
        """Say which key, the version the save named and the one there now — in the message,
        and as attributes a caller merges with."""
        super().__init__(
            f"{key} is at version {current}, not {expected}: read it again before saving"
        )
        self.key = key
        self.expected = expected
        self.current = current


class DraftConflict(EntryConflict):
    """A save named a version that is no longer the draft's: another tab, another device, saved
    in between, or the draft expired. The caller read before someone else wrote: read again,
    merge or reload, and save with the version read.

    `key` is the draft's subject; `current` is the version there now — `0` when there is no
    live draft.
    """
