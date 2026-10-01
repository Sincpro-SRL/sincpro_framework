"""What a record keeps about its relations, and the words a cascade decides by — no I/O.

RESOLVED / HELD / READ     where a record keeps a relation's value, how it got there, and
                           the identities its last whole reading or write held
Held                       assigned, blind, read whole, read cut
Orphans                    what a child its root no longer holds becomes
REPOSITORY                 the session key a unit of work leaves itself under, so a relation
                           touched inside the block resolves through it
"""

from enum import StrEnum
from typing import Any

RESOLVED = "_sincpro_resolved"
"""Where a record keeps the relations that were resolved for it, by name."""

HELD = "_sincpro_held"
"""How each relation of a record came to hold its value, by name — what a cascade trusts."""

READ = "_sincpro_read"
"""The identities a relation held when it was last read whole, or written, by name."""

REPOSITORY = "sincpro_repository"
"""The key under which a unit of work leaves its repository on the session, so a relation
touched inside the block can resolve itself."""


class Orphans(StrEnum):
    """What a child becomes when its root is removed, or no longer holds it.

        REFUSE    the default: refused, naming the relation — nothing is deleted or detached
                  because nobody said so
        DELETE    its row is deleted with the root (a cascade)
        DETACH    its key is set to NULL and the row stays

    Context: Ecto's `on_replace: :raise` and EF Core's `Restrict` — a delete nobody declared is
    the one outcome that cannot be taken back, so the relation says what removing does.
    """

    REFUSE = "refuse"
    DELETE = "delete"
    DETACH = "detach"


class Held(StrEnum):
    """How a relation's value came to be on a record, which decides what a write may infer
    from it.

        ASSIGNED    set by the domain over a whole reading: the set as it now stands
        BLIND       set with no whole reading behind it — what it replaced is not known
        WHOLE       resolved whole, or assigned and already written
        CUT         resolved through a specification that filtered or paged it

    Context: only an assignment over a whole reading says which children are gone, and only
    the ones that reading saw: a child another transaction added after it is not one of them.
    """

    ASSIGNED = "assigned"
    BLIND = "blind"
    WHOLE = "whole"
    CUT = "cut"


def held(record: Any, name: str) -> Held | None:
    """How this relation's value came to be on the record; `None` when nothing put one there."""
    return record.__dict__.get(HELD, {}).get(name)


def read_before(record: Any, name: str) -> frozenset[Any]:
    """The identities this relation held when it was last read whole or written."""
    return record.__dict__.get(READ, {}).get(name, frozenset())


def hold(
    record: Any, name: str, value: Any, how: Held, read: frozenset[Any] | None = None
) -> None:
    record.__dict__.setdefault(RESOLVED, {})[name] = value
    record.__dict__.setdefault(HELD, {})[name] = how
    if read is not None:
        record.__dict__.setdefault(READ, {})[name] = read
