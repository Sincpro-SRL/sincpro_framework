"""The Data Mapper: where an aggregate lives is declared once, here, and the aggregate never
learns it. A plain dataclass in the project's `domain/`, a table here, one call that joins them;
the Active
Record alternative, where the class knows its table, is the coupling this module exists to
avoid.

    metadata = MetaData()
    note_table = entity_table("note", metadata, Column("title", Text, nullable=False))
    map_aggregates(registry(), {Note: note_table})

Imperative mapping, so a project's `domain/` stays plain dataclasses with no import from here. Two things
this saves every project from writing again: the four `Entity` columns per table, and the
guard against mapping a class twice — a context can be built more than once in one process,
and `map_imperatively` raises on the second time.

**An `Entity` is mapped with its `version` as SQLAlchemy's `version_id_col`.** That is what
turns `save` into a conditional update: the UPDATE carries `WHERE version = :loaded` and a
row that moved on answers zero rows, which the engine reports as `StaleAggregate`. The ORM
already had the machinery; this is the line that switches it on.
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import MISSING, fields, is_dataclass
from typing import Any

import sqlalchemy
from sqlalchemy import (
    Table,
)
from sqlalchemy.orm import object_session, registry

from sincpro_framework.data_layer.orm.sqlalchemy.domain.registry import (
    RELATIONS,
)
from sincpro_framework.data_layer.orm.sqlalchemy.domain.relations import (
    REPOSITORY,
    RESOLVED,
    Held,
    Relation,
    held,
    hold,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.data_layer.orm.sqlalchemy.services.relation_resolver import (
    resolve_whole,
)
from sincpro_framework.ddd.entity import relations
from sincpro_framework.ddd.entity.entity_collection import identity_name
from sincpro_framework.ddd.entity.entity_meta import (
    annotations_of,
    related_class,
    without_optional,
)
from sincpro_framework.ddd.entity.relations import key_pair
from sincpro_framework.ddd.exceptions import RelationNotResolved
from sincpro_framework.ddd.query import serialising


class RelatedAttribute:
    """The attribute a declared relation becomes on the class.

        resolved for this record   →  what was resolved: a collection, a record, or None
        inside a unit of work      →  resolves now, whole, through the repository on the session
        anywhere else              →  RelationNotResolved

    A data descriptor, so it wins over the value a dataclass `__init__` stores: a record built
    in memory keeps what its constructor was given, a record the mapper loaded starts unresolved.
    """

    def __init__(
        self, name: str, relation: relations.Relation, default: Callable[[], Any]
    ) -> None:
        self.name = name
        self.relation = relation
        self.default = default

    def __get__(self, record: Any, owner: type | None = None) -> Any:
        if record is None:
            return self
        if serialising():
            # The answer writes the resolved relations itself; here pydantic sees the
            # declared shape, so it neither warns nor triggers a resolution.
            return self.default()
        session = object_session(record)
        repository = session.info.get(REPOSITORY) if session is not None else None
        resolved = record.__dict__.get(RESOLVED)
        if resolved is not None and self.name in resolved:
            value = resolved[self.name]
            # A value cut for a client — a page, a filter — is not what a Feature inside a unit
            # of work decides on: it gets the whole relation, whatever was left on the record.
            if (
                held(record, self.name) is not Held.CUT
                or session is None
                or repository is None
            ):
                return value
        if session is None or repository is None:
            raise RelationNotResolved(
                f"{type(record).__name__}.{self.name} was not asked for; name it in the "
                "criteria's specification, or read it inside context()"
            )

        resolve_whole(repository.prepare, session, type(record), record, self.name)
        answered = record.__dict__.get(RESOLVED, {})
        if self.name not in answered:
            # The resolver ran and wrote nothing, which a correct declaration never does. The
            # usual cause is a `Relation` pointing the wrong way — `foreign_key` names the
            # column on *this* aggregate, so declaring it for a to-many reads as a to-one that
            # matches nothing. Left alone this surfaced as `KeyError: '_sincpro_resolved'`.
            raise RelationNotResolved(
                f"{type(record).__name__}.{self.name} resolved to nothing. Check how it is "
                "declared: `foreign_key` names a column on this aggregate pointing at the "
                "other one, and a to-many is the foreign key on the other side — which "
                "`map_aggregates` infers on its own when the column declares it"
            )
        return answered[self.name]

    def holds_whole(self, record: Any) -> bool:
        """Whether the record holds this relation whole, answered without reading anything.

            resolved whole, or assigned over a whole reading    →  True
            cut by a specification, assigned blind, not read    →  False

        What a save asks before a derivation reads the relation: a save never triggers a read,
        and never computes a total over lines it does not hold.
        """
        resolved = record.__dict__.get(RESOLVED)
        if resolved is None or self.name not in resolved:
            return False
        return held(record, self.name) in (Held.WHOLE, Held.ASSIGNED)

    def held_in_part(self, record: Any) -> str:
        """How the record holds part of this relation and not all of it, answered without
        reading anything: `"cut"`, `"blind"`, or `""` — held whole, or not held at all.

            resolved through a specification that filtered it   →  "cut"
            assigned with no whole reading behind it            →  "blind"
            resolved whole, assigned over one, or never read     →  ""

        What a save asks to tell a derivation it skipped apart from one it can never miss: a
        relation never read cannot have changed; one held in part may have.
        """
        resolved = record.__dict__.get(RESOLVED)
        if resolved is None or self.name not in resolved:
            return ""
        state = held(record, self.name)
        return state.value if state is Held.CUT or state is Held.BLIND else ""

    def joined_by(self, record: Any) -> tuple[str, str] | None:
        """How a new record of this relation is tied to `record`, when a save of `record` writes
        it: `record`'s field, and the field of the new record that holds its value.

            Sale.lines       →  ("id", "sale_id")    a new line takes the sale's id
            Sale.customer    →  None                 another aggregate, not written from here

        What `assign` asks before building a line a form sent without its key: the form cannot
        know it, least of all for a sale that is not stored yet.
        """
        if not self._written_by_root(record):
            return None
        return key_pair(identity_name(type(record)), self.relation, True)

    def _written_by_root(self, record: Any) -> bool:
        """Whether a save of the record writes this relation — the only case its reading is
        worth a query before an assignment."""
        if (
            self.relation.kind != "foreign_key"
            or getattr(self.relation, "owned", None) is False
        ):
            return False
        annotation = annotations_of(type(record)).get(self.name)
        return annotation is not None and related_class(annotation)[1]

    def __set__(self, record: Any, value: Any) -> None:
        """An assignment replaces the set. Context: inside a unit of work, a stored record whose
        relation was not read whole reads it first — as Rails' `collection=` does — so what it
        replaced is known; anywhere else that is not possible, and the assignment is blind."""
        state = sqlalchemy.inspect(record, raiseerr=False)
        stored = state is not None and state.has_identity
        before = held(record, self.name)
        if (
            stored
            and before is not Held.WHOLE
            and before is not Held.ASSIGNED
            and self._written_by_root(record)
        ):
            session = object_session(record)
            repository = session.info.get(REPOSITORY) if session is not None else None
            if session is not None and repository is not None:

                resolve_whole(repository.prepare, session, type(record), record, self.name)
                before = held(record, self.name)
        whole = not stored or before is Held.WHOLE or before is Held.ASSIGNED
        hold(record, self.name, value, Held.ASSIGNED if whole else Held.BLIND)


def _dataclass_default(declared: Any) -> Callable[[], Any] | None:
    """What a dataclass field's `__init__` would have put there, or `None` when it has no
    default."""
    if declared.default_factory is not MISSING:
        return declared.default_factory
    if declared.default is not MISSING:
        return lambda value=declared.default: value
    return None


def _default_of(aggregate: type, name: str) -> Callable[[], Any]:
    """The default of one field, or `None` when the class is not a dataclass or the field has
    none."""
    if is_dataclass(aggregate):
        for declared in fields(aggregate):
            if declared.name == name:
                return _dataclass_default(declared) or (lambda: None)
    return lambda: None


def with_transient_defaults(
    aggregate: type, columns: Collection[str]
) -> Callable[[Any, Any], None]:
    """What a loaded record is missing: the dataclass fields that are neither a column nor a
    relation, a derived or in-memory value, get the default their `__init__` would have given.

    The mapper builds a record without calling `__init__`, so without this a field like
    `drafts: list[Draft] = field(default_factory=list)` would simply not exist on the record.
    """
    declared_fields = list(fields(aggregate)) if is_dataclass(aggregate) else []
    transient = {
        declared.name: make
        for declared in declared_fields
        if declared.name not in columns and (make := _dataclass_default(declared)) is not None
    }
    # A column the row left NULL for a field that does not say it may be absent. The mapper
    # reports the NULL faithfully, and the domain gets `None` where it declares `list[str]` —
    # the first place that touches it raises, far from the row that caused it. A field that
    # declared `| None` is left alone: there the NULL is the answer.
    nullable = {
        declared.name: make
        for declared in declared_fields
        if declared.name in columns
        and declared.type is without_optional(declared.type)
        and (make := _dataclass_default(declared)) is not None
    }

    def fill(record: Any, _context: Any) -> None:
        for name, make in transient.items():
            if name in RELATIONS.get(aggregate, {}) or name in record.__dict__:
                continue
            record.__dict__[name] = make()
        for name, make in nullable.items():
            if record.__dict__.get(name, "") is None:
                record.__dict__[name] = make()

    return fill


def install(aggregate: type, declared: Mapping[str, relations.Relation]) -> None:
    """Registers the relations and puts the attribute that resolves them on the class. What
    was declared wins over what is inferred; a second call changes nothing."""
    known = RELATIONS.setdefault(aggregate, {})
    added = False
    for name, relation in declared.items():
        if name in known:
            continue
        known[name] = relation
        added = True
        setattr(
            aggregate, name, RelatedAttribute(name, relation, _default_of(aggregate, name))
        )
    if added:
        # `describe` caches per class; a definition read before this point knew no relations.

        describe.cache_clear()


def inferred_foreign_keys(mapper_registry: registry, aggregate: type) -> dict[str, Relation]:
    """The relations the tables already say, so nobody declares them twice.

        Work.author: Author | None   and  work.author_id  →  ForeignKey("author.id")
                                     →  Relation.foreign_key(Author, identified_by="author_id")
        Author.works: list[Work]     and that same foreign key, seen from the other side
                                     →  Relation.foreign_key(Work, identified_by="author_id")

    An annotation pointing at a mapped class whose table is tied to this one by a foreign key
    is a relation. With several foreign keys between the two tables the column named after the
    attribute, `<name>_id`, decides; with none of those, nothing is inferred and the data
    mapper has to be told.

    A foreign key to a unique column that is not the primary key names both sides, so the
    match reads the column the key references instead of the identity:

        Repo.workspace_id  →  ForeignKey("ws.workspace_id")
                           →  Ws.repositories by parent_field="workspace_id",
                              related_field="workspace_id"
    """
    tables = {mapper.class_: mapper.local_table for mapper in mapper_registry.mappers}
    own = tables.get(aggregate)
    if own is None:
        return {}
    inferred: dict[str, Relation] = {}
    for name, annotation in annotations_of(aggregate).items():
        if name in own.c:
            continue
        related, many = related_class(annotation)
        if related is None or related not in tables:
            continue
        holder, points_at = (tables[related], own) if many else (own, tables[related])
        candidates = {
            column.name: fk.column
            for column in holder.c
            for fk in column.foreign_keys
            if fk.column.table is points_at
        }
        if len(candidates) > 1:
            preferred = f"{name}_id" if not many else f"{getattr(own, 'name', '')}_id"
            candidates = {c: r for c, r in candidates.items() if c == preferred}
        if len(candidates) != 1:
            continue
        [(holding, referenced)] = candidates.items()
        if referenced.primary_key:
            inferred[name] = Relation.foreign_key(related, identified_by=holding)
        elif many:
            inferred[name] = Relation.foreign_key(
                related, parent_field=referenced.name, related_field=holding
            )
        else:
            inferred[name] = Relation.foreign_key(
                related, parent_field=holding, related_field=referenced.name
            )
    return inferred


def parent_table(mapper_registry: registry, parent: type) -> Table:
    for mapper in mapper_registry.mappers:
        if mapper.class_ is parent and isinstance(mapper.local_table, Table):
            return mapper.local_table
    raise ValueError(f"{parent.__name__} is not mapped to a table")


def refuse_a_table_that_does_not_extend(
    entity: type, table: Table, parent: type, parent_table: Table
) -> None:
    """Context: an extension's row is joined to its parent's by the key, so its table must
    reference the parent's primary key; without that there is nothing to join on."""
    keys = {column for column in parent_table.primary_key.columns}
    if not any(fk.column in keys for fk in table.foreign_keys):
        wanted = ", ".join(f"{parent_table.name}.{column.name}" for column in keys)
        raise ValueError(
            f"{entity.__name__} extends {parent.__name__}, so its table {table.name} holds only "
            f"its own columns and must reference {wanted} as its primary key"
        )
