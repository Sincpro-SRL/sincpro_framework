"""The Data Mapper: where an aggregate lives is declared once, here, and the aggregate never
learns it. A plain dataclass in `domain/`, a table here, one call that joins them; the Active
Record alternative, where the class knows its table, is the coupling this module exists to
avoid.

    metadata = MetaData()
    note_table = entity_table("note", metadata, Column("title", Text, nullable=False))
    map_aggregates(registry(), {Note: note_table})

Imperative mapping, so `domain/` stays plain dataclasses with no import from here. Two things
this saves every project from writing again: the four `Entity` columns per table, and the
guard against mapping a class twice — a context can be built more than once in one process,
and `map_imperatively` raises on the second time.

**An `Entity` is mapped with its `version` as SQLAlchemy's `version_id_col`.** That is what
turns `save` into a conditional update: the UPDATE carries `WHERE version = :loaded` and a
row that moved on answers zero rows, which the engine reports as `StaleAggregate`. The ORM
already had the machinery; this is the line that switches it on.
"""

from collections.abc import Callable, Mapping
from dataclasses import MISSING, fields, is_dataclass
from typing import Any

from sqlalchemy import Column, DateTime, Integer, MetaData, Table, Text, event
from sqlalchemy.orm import object_session, registry
from sqlalchemy.types import TypeEngine

from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity_collection import EntityCollection
from sincpro_framework.ddd.exceptions import RelationNotResolved
from sincpro_framework.ddd.model_meta import annotations_of, related_class
from sincpro_framework.ddd.relations import Relation as DeclaredRelation
from sincpro_framework.ddd.relations import Resolver

RESOLVED = "_sincpro_resolved"
"""Where a record keeps the relations that were resolved for it, by name."""

REPOSITORY = "sincpro_repository"
"""The key under which a unit of work leaves its repository on the session, so a relation
touched inside the block can resolve itself."""

# What an `Entity` column is called on disk, in the order the class declares them.
ENTITY_COLUMNS: tuple[str, ...] = ("id", "created_at", "updated_at", "version")


def entity_columns(datetime_type: TypeEngine | None = None) -> list[Column]:
    """The four columns every `Entity` table starts with.

        out     id TEXT PRIMARY KEY · created_at NOT NULL · updated_at · version INTEGER NOT NULL

    `datetime_type` is what a timestamp is stored as — a real `DateTime` by default, or a
    project's own decorator when its tables predate this layer and keep ISO text.
    """
    moment = datetime_type if datetime_type is not None else DateTime(timezone=True)
    return [
        Column("id", Text, primary_key=True),
        Column("created_at", moment, nullable=False),
        Column("updated_at", moment),
        Column("version", Integer, nullable=False, default=1),
    ]


def entity_table(
    name: str,
    metadata: MetaData,
    *columns: Any,
    datetime_type: TypeEngine | None = None,
) -> Table:
    """A table for an `Entity` subclass: the four base columns, then the aggregate's own.

        in      "note", metadata, Column("title", Text), Index("note_title", "title")
        out     Table("note", id, created_at, updated_at, version, title, + the index)

    Anything `Table` accepts after its columns — an `Index`, a constraint — passes through.
    """
    return Table(name, metadata, *entity_columns(datetime_type), *columns)


class Relation(DeclaredRelation):
    """The declaration from `sincpro_framework.ddd.relations`, plus the kinds only a database
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
        identified_by: str,
        resolver: Resolver | None = None,
        through: Table | None = None,
        related_key: str | None = None,
    ) -> None:
        super().__init__(kind, related, identified_by, resolver)
        self.through = through
        self.related_key = related_key

    @classmethod
    def foreign_key(cls, related: type, identified_by: str) -> "Relation":
        """Same database: a column on one side holds the other side's identity."""
        return cls("foreign_key", related, identified_by)

    @classmethod
    def many_to_many(
        cls, related: type, through: Table, this_key: str, related_key: str
    ) -> "Relation":
        """Same database: the pairs live in `through`, `this_key` pointing here and
        `related_key` pointing at the related aggregate."""
        return cls(
            "many_to_many", related, this_key, through=through, related_key=related_key
        )

    @classmethod
    def id_list(cls, related: type, identified_by: str) -> "Relation":
        """Same database, no foreign key: this aggregate holds a list of the related ids."""
        return cls("id_list", related, identified_by)


RELATIONS: dict[type, dict[str, DeclaredRelation]] = {}


def relations_of(aggregate: type) -> dict[str, DeclaredRelation]:
    """What was declared for this aggregate, by relation name; empty when nothing was."""
    return RELATIONS.get(aggregate, {})


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
            partial = isinstance(value, EntityCollection) and value.is_partial
            # A page cut for a client is not what a Feature inside a unit of work reads: it
            # gets the whole relation, whatever a specification left on the record before.
            if not partial or session is None or repository is None:
                return value
        if session is None or repository is None:
            raise RelationNotResolved(
                f"{type(record).__name__}.{self.name} was not asked for; name it in the "
                "criteria's specification, or read it inside context()"
            )
        from sincpro_framework.orm.sqlalchemy.relation_resolver import resolve_whole

        resolve_whole(repository, session, type(record), record, self.name)
        return record.__dict__[RESOLVED][self.name]

    def __set__(self, record: Any, value: Any) -> None:
        record.__dict__.setdefault(RESOLVED, {})[self.name] = value


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

    """
    already = {mapper.class_ for mapper in mapper_registry.mappers}
    for entity, table in tables.items():
        if entity in already:
            continue

        options: dict[str, Any] = {}
        if properties and entity in properties:
            options["properties"] = properties[entity]
        if issubclass(entity, Entity) and "version" in table.c:
            options["version_id_col"] = table.c.version

        mapper = mapper_registry.map_imperatively(entity, table, **options)
        event.listen(mapper, "load", _with_transient_defaults(entity, table))

    for aggregate, declared in (relations or {}).items():
        _install(aggregate, declared)
    # Every mapped class, not only this call's: a foreign key between a class mapped earlier
    # and one mapped now is a relation on both, and both are seen on the second call.
    for mapper in mapper_registry.mappers:
        _install(mapper.class_, _inferred_foreign_keys(mapper_registry, mapper.class_))


def _with_transient_defaults(aggregate: type, table: Table) -> Callable[[Any, Any], None]:
    """What a loaded record is missing: the dataclass fields that are neither a column nor a
    relation, a derived or in-memory value, get the default their `__init__` would have given.

    The mapper builds a record without calling `__init__`, so without this a field like
    `drafts: list[Draft] = field(default_factory=list)` would simply not exist on the record.
    """
    transient = {
        declared.name: make
        for declared in (fields(aggregate) if is_dataclass(aggregate) else ())
        if declared.name not in table.c and (make := _dataclass_default(declared)) is not None
    }

    def fill(record: Any, _context: Any) -> None:
        for name, make in transient.items():
            if name in RELATIONS.get(aggregate, {}) or name in record.__dict__:
                continue
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
        from sincpro_framework.orm.sqlalchemy.model_introspection import describe

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
        candidates = [
            column.name
            for column in holder.c
            if any(fk.column.table is points_at for fk in column.foreign_keys)
        ]
        if len(candidates) > 1:
            preferred = f"{name}_id" if not many else f"{getattr(own, 'name', '')}_id"
            candidates = [c for c in candidates if c == preferred]
        if len(candidates) == 1:
            inferred[name] = Relation.foreign_key(related, identified_by=candidates[0])
    return inferred
