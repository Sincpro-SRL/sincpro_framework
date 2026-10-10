"""What a unit of work is opened with. `infrastructure/transaction_opening.py` applies it before
any statement runs — the only time an isolation level can still be chosen.

    Transaction(isolation=Isolation.SERIALIZABLE)    a balance two requests must not both pass
    Transaction(read_only=True)                a report: every write refused
    Transaction(timeout=5.0)                   seconds any one statement may run (Postgres)
    Transaction(engine={...})                  SQLAlchemy's `execution_options`, as they come
    Transaction(writes=Writes.CHANGED)         the commit also writes what changed unsaved
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Isolation(StrEnum):
    """How much of what other transactions do a unit of work may see while it runs."""

    READ_COMMITTED = "read_committed"
    REPEATABLE_READ = "repeatable_read"
    SERIALIZABLE = "serializable"


class Writes(StrEnum):
    """What the commit of a unit of work writes.

        SAVED      only what `save` wrote — the default: a write is a call, never a side effect
        CHANGED    also what the block changed on what it loaded, as the session tracks it

    Context: the repository is persistence-oriented (Vernon) — it has a `save` — so a change that
    never reached it bypassed the aggregate's hooks and cascade unseen. `CHANGED` is Fowler's
    object registration, for a block that wants it; `SAVED` is caller registration, as Doctrine's
    `DEFERRED_EXPLICIT`, Django, Rails and Ecto write.
    """

    SAVED = "saved"
    CHANGED = "changed"


ISOLATION_LEVELS: dict[Isolation, str] = {
    Isolation.READ_COMMITTED: "READ COMMITTED",
    Isolation.REPEATABLE_READ: "REPEATABLE READ",
    Isolation.SERIALIZABLE: "SERIALIZABLE",
}


@dataclass(frozen=True)
class Transaction:
    """What a unit of work was opened with. Empty is what the database does on its own."""

    isolation: Isolation | None = None
    read_only: bool = False
    timeout: float | None = None
    engine: Mapping[str, Any] = field(default_factory=dict)
    writes: Writes = Writes.SAVED
