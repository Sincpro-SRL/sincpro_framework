"""What a record holds of its relations, and the words a cascade decides by — no I/O.

HeldRelations              a relation's value on a record, how it got there, and the
                           identities its last whole reading or write held
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


class HeldRelations:
    """What one record holds of its relations: each value, how it got there, and the
    identities its last whole reading or write held — stored on the record itself, beside its
    columns, so it lives and dies with the record.

        relations = HeldRelations(invoice)
        relations.hold("lines", lines, Held.WHOLE, read=frozenset({1, 2}))
        relations.holds("lines")        ->  True
        relations.value("lines")        ->  lines
        relations.held("lines")         ->  Held.WHOLE
        relations.read_before("lines")  ->  frozenset({1, 2})

    Context: `sincpro_framework.ddd.query` reads the values under the same `RESOLVED` key; it
    cannot import this adapter, so the key is the contract between them.
    """

    RESOLVED = "_sincpro_resolved"
    """Where a record stores the relations that were resolved for it, by name."""

    HELD = "_sincpro_held"
    """How each relation of a record came to hold its value, by name — what a cascade trusts."""

    READ = "_sincpro_read"
    """The identities a relation held when it was last read whole, or written, by name."""

    def __init__(self, record: Any) -> None:
        self._state = record.__dict__

    def holds(self, name: str) -> bool:
        """Whether a value of this relation is on the record — resolved or assigned."""
        return name in self._state.get(self.RESOLVED, {})

    def value(self, name: str) -> Any:
        """The value of this relation on the record; `KeyError` when none is held."""
        return self._state[self.RESOLVED][name]

    def held(self, name: str) -> Held | None:
        """How this relation's value came to be on the record; `None` when nothing put one
        there."""
        return self._state.get(self.HELD, {}).get(name)

    def read_before(self, name: str) -> frozenset[Any]:
        """The identities this relation held when it was last read whole or written."""
        return self._state.get(self.READ, {}).get(name, frozenset())

    def hold(
        self, name: str, value: Any, how: Held, read: frozenset[Any] | None = None
    ) -> None:
        """Puts a relation's value on the record, saying how it got there — what a cascade
        reads later.

        Context: a write decides from `how` whether the value can settle the children it no
        longer holds (`ASSIGNED`, `WHOLE`) or must not infer anything (`BLIND`, `CUT`). `read`
        is stored only when given, so a blind assignment leaves the identities of the last
        whole reading as they were.
        """
        self._state.setdefault(self.RESOLVED, {})[name] = value
        self._state.setdefault(self.HELD, {})[name] = how
        if read is not None:
            self._state.setdefault(self.READ, {})[name] = read


class TableRelation(relations.Relation):
    """The declaration from `sincpro_framework.ddd.entity.relations`, plus the kinds only a database
    has, declared once beside the table:

        TableRelation.foreign_key(Run, identified_by="dataset_id")          Dataset.runs · Run.dataset
        TableRelation.many_to_many(Tag, through=dataset_tag, this_key="dataset_id", related_key="tag_id")
        TableRelation.id_list(Dataset, identified_by="source_ids")           ids held in a JSON column
        TableRelation.bus(Run, execution, CommandSearchRuns, identified_by="dataset_id")
        TableRelation.resolved_by(Publisher, identified_by="publisher_id", resolver=fetch)

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
        many: bool | None = None,
    ) -> None:
        """Prefer the named constructors (`foreign_key`, `many_to_many`, ...): this one takes
        every kind's options at once. `through` and `related_key` are read by `many_to_many`
        only; `owned` and `orphans` by a to-many `foreign_key` only.

            TableRelation("many_to_many", Tag, "dataset_id", through=dataset_tag,
                     related_key="tag_id")
                ->  as TableRelation.many_to_many(Tag, dataset_tag, "dataset_id", "tag_id")

        The options every kind shares go to the base declaration; the database's own —
        `through`, `related_key`, `owned`, `orphans` — are held here.
        """
        super().__init__(
            kind,
            related,
            identified_by,
            resolver,
            scope=scope,
            parent_field=parent_field,
            related_field=related_field,
            many=many,
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
        many: bool | None = None,
    ) -> "TableRelation":
        """Same database: a column on one side holds the other side's identity.

        `many` says the cardinality when the class cannot: an extension installing a relation on an
        entity it does not own passes `many=False` and the attribute is typed `Related | None`,
        where it used to write the entity's `__annotations__` itself.

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
            many=many,
        )

    @classmethod
    def many_to_many(
        cls,
        related: type,
        through: Table,
        this_key: str,
        related_key: str,
        scope: Criteria | None = None,
    ) -> "TableRelation":
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
    ) -> "TableRelation":
        """Same database, no foreign key: this aggregate holds a list of the related ids."""
        return cls("id_list", related, identified_by, scope=scope)
