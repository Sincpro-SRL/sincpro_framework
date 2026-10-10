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

from sqlalchemy import Table

from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.entity import relations
from sincpro_framework.ddd.entity.relations import Resolver

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


class Relation(relations.Relation):
    """The declaration from `sincpro_framework.ddd.entity.relations`, plus the kinds only a database
    has, declared once beside the table:

        Relation.foreign_key(Run, identified_by="dataset_id")          Dataset.runs · Run.dataset
        Relation.many_to_many(Tag, through=dataset_tag, this_key="dataset_id", related_key="tag_id")
        Relation.id_list(Dataset, identified_by="source_ids")           ids held in a JSON column
        Relation.bus(Run, execution, CommandSearchRuns, identified_by="dataset_id")
        Relation.resolved_by(Publisher, identified_by="publisher_id", resolver=fetch)

    Every kind is resolved once per node for a whole page, never per row.
    """

    def __init__(
        self,
        kind: str,
        related: type,
        identified_by: str = "",
        resolver: Resolver | None = None,
        through: Table | None = None,
        related_key: str | None = None,
        scope: Criteria | None = None,
        parent_field: str | None = None,
        related_field: str | None = None,
        owned: bool = True,
        orphans: Orphans = Orphans.REFUSE,
    ) -> None:
        super().__init__(
            kind,
            related,
            identified_by,
            resolver,
            scope=scope,
            parent_field=parent_field,
            related_field=related_field,
        )
        self.through = through
        self.related_key = related_key
        self.owned = owned
        self.orphans = orphans

    @classmethod
    def foreign_key(
        cls,
        related: type,
        identified_by: str = "",
        scope: Criteria | None = None,
        parent_field: str | None = None,
        related_field: str | None = None,
        owned: bool = True,
        orphans: Orphans = Orphans.REFUSE,
    ) -> "Relation":
        """Same database: a column on one side holds the other side's identity.

        **A to-many is part of its root unless it says otherwise** — Evans' aggregate: `save`
        writes the children with the root's key on them, an assignment over a whole reading
        settles the ones it no longer holds, and `remove` takes them along. `owned=False` makes
        it a reference to another aggregate, read and never written — Vernon's rule is to hold
        those by id, so a reference collection is a navigation, not a part. `orphans` says what
        removing the root — or a child from it — does: `Orphans.REFUSE` by default, so nothing
        is deleted or detached that nobody declared; `Orphans.DELETE` to take the children with
        it, `Orphans.DETACH` to set their key to NULL. A to-one is never owned: a child does not
        own its parent, and `owned` is read only on a to-many.
        """
        return cls(
            "foreign_key",
            related,
            identified_by,
            scope=scope,
            parent_field=parent_field,
            related_field=related_field,
            owned=owned,
            orphans=orphans,
        )

    @classmethod
    def many_to_many(
        cls,
        related: type,
        through: Table,
        this_key: str,
        related_key: str,
        scope: Criteria | None = None,
    ) -> "Relation":
        """Same database: the pairs live in `through`, `this_key` pointing here and
        `related_key` pointing at the related aggregate."""
        return cls(
            "many_to_many",
            related,
            this_key,
            through=through,
            related_key=related_key,
            scope=scope,
        )

    @classmethod
    def id_list(
        cls, related: type, identified_by: str, scope: Criteria | None = None
    ) -> "Relation":
        """Same database, no foreign key: this aggregate holds a list of the related ids."""
        return cls("id_list", related, identified_by, scope=scope)
