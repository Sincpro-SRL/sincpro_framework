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

from sqlalchemy import (
    Table,
    event,
)
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import object_session, registry

from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.model_meta import (
    annotations_of,
    related_class,
    without_optional,
)
from sincpro_framework.ddd.entity.relations import Relation as DeclaredRelation
from sincpro_framework.ddd.entity.relations import Resolver
from sincpro_framework.ddd.exceptions import RelationNotResolved
from sincpro_framework.orm.sqlalchemy.domain.registry import RELATIONS, record_mapping
from sincpro_framework.orm.sqlalchemy.domain.relations import (
    REPOSITORY,
    RESOLVED,
    Held,
    Orphans,
    held,
    hold,
)


class Relation(DeclaredRelation):
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


class RelatedAttribute:
    """The attribute a declared relation becomes on the class.

        resolved for this record   →  what was resolved: a collection, a record, or None
        inside a unit of work      →  resolves now, whole, through the repository on the session
        anywhere else              →  RelationNotResolved

    A data descriptor, so it wins over the value a dataclass `__init__` stores: a record built
    in memory keeps what its constructor was given, a record the mapper loaded starts unresolved.
    """

    def __init__(
        self, name: str, relation: DeclaredRelation, default: Callable[[], Any]
    ) -> None:
        self.name = name
        self.relation = relation
        self.default = default

    def __get__(self, record: Any, owner: type | None = None) -> Any:
        if record is None:
            return self
        from sincpro_framework.ddd.query import serialising

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
        from sincpro_framework.orm.sqlalchemy.services.relation_resolver import resolve_whole

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
        state = sa_inspect(record, raiseerr=False)
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
                from sincpro_framework.orm.sqlalchemy.services.relation_resolver import (
                    resolve_whole,
                )

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


def _with_transient_defaults(
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


def _install(aggregate: type, declared: Mapping[str, DeclaredRelation]) -> None:
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
        from sincpro_framework.orm.sqlalchemy.services.model_introspection import describe

        describe.cache_clear()


def _inferred_foreign_keys(mapper_registry: registry, aggregate: type) -> dict[str, Relation]:
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


def _parent_table(mapper_registry: registry, parent: type) -> Table:
    for mapper in mapper_registry.mappers:
        if mapper.class_ is parent and isinstance(mapper.local_table, Table):
            return mapper.local_table
    raise ValueError(f"{parent.__name__} is not mapped to a table")


def _refuse_a_table_that_does_not_extend(
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


def map_aggregates(
    mapper_registry: registry,
    tables: dict[type, Table],
    properties: dict[type, dict[str, Any]] | None = None,
    relations: dict[type, dict[str, DeclaredRelation]] | None = None,
) -> None:
    """Maps each class to its table, once, switching on the version check for an `Entity`, and
    installs the relations on the classes: the declared ones, then the ones the foreign keys
    already say.

        in      registry, {Note: note_table, Dataset: dataset_table}
        out     both mapped; calling it again changes nothing

    `properties` is per class and is what `map_imperatively` takes: how to map an attribute
    onto a column with another name, say `{"id": dataset_table.c.dataset_id}` for a table
    that predates the `Entity` convention.

    `relations` is what the tables cannot say on their own — a kind that needs a bus, a
    function or a scope. Whatever is declared here wins over what a foreign key infers.

    A class that extends another mapped aggregate is mapped as its extension: the fields it
    inherits stay in the parent's table, its table holds only its own columns and references
    the parent's key. Parents are mapped first, whatever the order of `tables`.
    """
    already = {mapper.class_ for mapper in mapper_registry.mappers}
    for entity, table in sorted(tables.items(), key=lambda item: len(item[0].__mro__)):
        if entity in already:
            continue

        options: dict[str, Any] = {}
        if properties and entity in properties:
            options["properties"] = properties[entity]
        parent = next((base for base in entity.__mro__[1:] if base in already), None)
        if parent is not None:
            _refuse_a_table_that_does_not_extend(
                entity, table, parent, _parent_table(mapper_registry, parent)
            )
            options["inherits"] = parent
        elif issubclass(entity, Entity) and "version" in table.c:
            options["version_id_col"] = table.c.version

        mapper = mapper_registry.map_imperatively(entity, table, **options)
        record_mapping(table, entity)
        event.listen(
            mapper, "load", _with_transient_defaults(entity, set(mapper.columns.keys()))
        )
        already.add(entity)

    for aggregate, declared in (relations or {}).items():
        _install(aggregate, declared)
    # Every mapped class, not only this call's: a foreign key between a class mapped earlier
    # and one mapped now is a relation on both, and both are seen on the second call.
    for mapper in mapper_registry.mappers:
        _install(mapper.class_, _inferred_foreign_keys(mapper_registry, mapper.class_))
