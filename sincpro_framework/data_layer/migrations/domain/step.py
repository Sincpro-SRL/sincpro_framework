"""The vocabulary: a chain of steps per (context × store), and where a store stands on it."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sincpro_framework.exceptions import ExternalServiceError, ProgrammingError


@dataclass(frozen=True)
class Chain:
    """One store of one context: its steps run in order, and it keeps its own position.

    Context: `folder` is where the store's step bodies live — `<context folder>/<store>`."""

    context: str
    store: str
    folder: Path

    @property
    def key(self) -> str:
        return f"{self.context}/{self.store}"


@dataclass(frozen=True)
class Step:
    """One migration of one chain. `id` is a UUIDv7 in hex, so ids sort by when they were made;
    `file` is its body, relative to its chain's folder — `chain.folder / step.file`; `requires`
    names steps of other chains — `context/store/id` — that must run first."""

    id: str
    context: str
    store: str
    parent: str | None
    message: str
    file: str
    checksum: str
    requires: tuple[str, ...] = ()
    irreversible: bool = False

    @property
    def chain_key(self) -> str:
        return f"{self.context}/{self.store}"

    @property
    def key(self) -> str:
        return f"{self.context}/{self.store}/{self.id}"


@dataclass(frozen=True)
class Position:
    """Where a store stands: the last step it applied — `None` before the first — and whether a
    step that could not run in a transaction failed part-way."""

    head: str | None
    dirty: bool = False


class ChainState(StrEnum):
    UP_TO_DATE = "up to date"
    BEHIND = "behind"
    AHEAD = "ahead"
    DIRTY = "dirty"


class MigrationRefused(ProgrammingError):
    """Nothing ran: the system is not where the command can start from, or it asked for
    something the code does not have."""


class MigrationFailed(ExternalServiceError):
    """A step failed; the steps before it stay applied, the ones after it did not run."""
