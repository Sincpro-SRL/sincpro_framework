"""The vocabulary: a chain of steps per (context × store), and where a store stands on it."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


@dataclass(frozen=True)
class Chain:
    """One store of one context: its steps run in order, and it has a position of its own.

    Context: `folder` is where the store's step bodies live — `<context folder>/<store>`."""

    context: str
    store: str
    folder: Path

    @property
    def key(self) -> str:
        """`context/store` — how a chain is named in a status, a refusal and a `requires`,
        and what `InMemoryEngine` keys its positions by.

            Chain("chat", "main", folder).key  ->  "chat/main"
        """
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
        """The key of the chain the step belongs to — `context/store`."""
        return f"{self.context}/{self.store}"

    @property
    def key(self) -> str:
        """The step's key across every chain — `context/store/id` — the form a `requires`
        names it in.

            step.key  ->  "chat/main/0192a1f3…"
        """
        return f"{self.context}/{self.store}/{self.id}"


@dataclass(frozen=True)
class Position:
    """Where a store stands: the last step it applied — `None` before the first — and whether a
    step that could not run in a transaction failed part-way."""

    head: str | None
    dirty: bool = False


class ChainState(StrEnum):
    """What a store's position means against the steps the code knows for its chain.

    - `UP_TO_DATE` — it stands on the last step (or there are none and it stands on none).
    - `BEHIND` — it stands on an earlier step, or on none: `upgrade` moves it.
    - `AHEAD` — it stands on a step the code does not have: a newer release, or an abandoned
      branch; every run is refused until it is downgraded with that release or resolved.
    - `DIRTY` — a step of a store without transactional DDL failed part-way; every run is
      refused until a human cleans it up and runs `resolve`.
    """

    UP_TO_DATE = "up to date"
    BEHIND = "behind"
    AHEAD = "ahead"
    DIRTY = "dirty"

    @classmethod
    def of(cls, steps: Sequence[Step], position: Position) -> "ChainState":
        """The state a store's position means against the steps its chain knows.

        Context: a head the code does not know is *ahead* — a step of a newer release, or of a
        branch that was abandoned; its id cannot tell which, since a step merged late still has
        an old one.

            steps a, b, c;  Position("c")             ->  UP_TO_DATE
            steps a, b, c;  Position("a")             ->  BEHIND
            steps a, b, c;  Position(None)            ->  BEHIND   (UP_TO_DATE with no steps)
            steps a, b, c;  Position("z")             ->  AHEAD
            any steps;      Position("b", dirty=True) ->  DIRTY
        """
        if position.dirty:
            return cls.DIRTY
        ids = [step.id for step in steps]
        if position.head is None:
            return cls.BEHIND if ids else cls.UP_TO_DATE
        if position.head in ids:
            return cls.UP_TO_DATE if position.head == ids[-1] else cls.BEHIND
        return cls.AHEAD
