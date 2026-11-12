"""Reads a mapped class into the definition a caller is told about.

The one place this layer asks SQLAlchemy what a class looks like. `Meta` itself is pure
vocabulary; this is the half that knows there is a mapper behind it — which columns exist,
which may be NULL, which annotation points at another mapped class.

    ModelIntrospection.describe(Dataset)   →   Meta(aggregate='Dataset', identity='dataset_id', …)

Cached, because mappings are applied once at boot and a table grows no column at runtime.
"""

from functools import cache
from typing import Any, TypeGuard

from sqlalchemy import UniqueConstraint, inspect
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.sql.expression import TextualSelect

from sincpro_framework.data_layer import exceptions
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.mapping_registry import (
    mapping_registry,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services.event_mapping import (
    is_mapped_event,
    map_new_event_classes,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services.extension_typing import (
    HIDDEN,
)
from sincpro_framework.ddd.entity import extensions
from sincpro_framework.ddd.entity.entity_meta import (
    FieldMeta,
    FieldType,
    Meta,
    logical_type,
    related_class,
    without_optional,
)
from sincpro_framework.ddd.entity.relations import RelationKind, key_pair
from sincpro_framework.ddd.metamodel import StoredField, StoreFacts, description_of, model_of
from sincpro_framework.ddd.multi_language import MultiLanguage


def _relational_type(kind: RelationKind | None, many: bool) -> FieldType:
    """The field type a relation is described as; one nothing declared (`None`) is read by its
    cardinality, as a foreign key is.

    (MANY_TO_MANY, _)         ->  MANY2MANY
    (FOREIGN_KEY, True)       ->  ONE2MANY
    (None, False)             ->  MANY2ONE
    """
    if kind is RelationKind.MANY_TO_MANY:
        return FieldType.MANY2MANY
    return FieldType.ONE2MANY if many else FieldType.MANY2ONE


def is_mapped(candidate: Any) -> TypeGuard[type]:
    """Whether the ORM maps this class.

        is_mapped(Dataset)       ->  True     it has a table
        is_mapped(str)           ->  False    a plain value
        is_mapped(Dataset(...))  ->  False    an instance, not a class

    SQLAlchemy is asked, so a class mapped by any registry counts.
    """
    if not isinstance(candidate, type):
        return False
    try:
        inspect(candidate)
        return True
    except NoInspectionAvailable:
        return False


def _column_type(column: Any) -> Any:
    """What a mapped column holds when the class does not say it — a column the mapping adds
    that no field declares, like a context's event table's delivery columns, or an expression an
    extension maps onto a class it does not own (`properties=`), which has no NOT NULL and so may
    be absent."""
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return Any
    return python_type | None if getattr(column, "nullable", True) else python_type


def _key_is_unique(entity: type, table: Any, key: str, identity: str) -> None:
    """A `DEFAULT_GET_ID` other than the identity must be unique on disk: a `Get` reads one
    record by it, and two rows sharing a key would make it answer whichever came first.

        in      Account(DEFAULT_GET_ID="code"), Column("code", Text, unique=True)    →  ok
        in      Account(DEFAULT_GET_ID="code"), Column("code", Text)                 →  refused

    Unique as the column says, or as a unique constraint or index on that column alone.
    """
    if not key or key == identity or key not in table.c:
        return
    column = table.c[key]
    alone = {
        tuple(one.columns.keys())
        for one in (*table.constraints, *table.indexes)
        if isinstance(one, UniqueConstraint) or getattr(one, "unique", False)
    }
    if column.primary_key or column.unique or (key,) in alone:
        return
    raise exceptions.ProgrammingError(
        f"{entity.__name__}.DEFAULT_GET_ID answers {key}, and {table.name}.{key} is not "
        f"unique: a Get reads one record by it. Declare the column unique=True."
    )


def _is_reference(relation: Any) -> bool:
    """Whether a to-many holds other aggregates rather than parts: a foreign key declared
    `owned=False`, or answered by another context."""
    if relation is None:
        return False
    if relation.kind is RelationKind.RESOLVER:
        return True
    return (
        relation.kind is RelationKind.FOREIGN_KEY
        and getattr(relation, "owned", True) is False
    )


class ModelIntrospection:
    """What this layer asks SQLAlchemy about a mapped class — the one place it does.

    ModelIntrospection.describe(Dataset)   ->  Meta(aggregate='Dataset', ...)
    """

    @staticmethod
    def facts(entity: type) -> StoreFacts:
        """What the mapper knows of `entity` that the entity does not say: its identity, its
        columns — what each holds and whether it may be NULL —, how each relation meets, and the
        fields its extensions add.

            ModelIntrospection.facts(Dataset)
                ->  StoreFacts(identity="dataset_id", stored={"name": …}, relations={"runs": …})

        1. Inspect the mapper; refuse a class with no table, since it has no fields to ask about.
        2. Each column but the hidden ones, held as its annotation says — or as the column does,
           for one no field declares — and empty as the column allows; a computed column has no
           NOT NULL, so its annotation says whether it may be absent.
        3. Any annotation pointing at a mapped class, or any relation the data mapper declared
           or inferred, is a relation: `many2one`, `one2many` or `many2many`, with the aggregate
           on the other side and the two fields it meets by, expanded — navigable when the
           database keeps it (a foreign key, a many-to-many), not when a resolver or a bus brings
           it.
        Final: the fields each extension adds, absent on a row that is not one.
        """
        try:
            mapper = inspect(entity)
        except NoInspectionAvailable:
            mapper = None
        if mapper is None and is_mapped_event(entity):
            map_new_event_classes()  # an event class declared after its base was mapped
            mapper = inspect(entity)
        if mapper is None:
            raise exceptions.ProgrammingError(
                f"{entity.__name__} is not a mapped aggregate; there is nothing to query"
            )

        annotations = model_of(entity).annotations
        identity = mapper.primary_key[0].name
        stored: dict[str, StoredField] = {}
        for column in mapper.columns:
            if column.key in HIDDEN:
                continue
            annotation = annotations.get(column.key) or _column_type(column)
            nullable = (
                bool(column.nullable)
                if hasattr(column, "nullable")
                else annotation != without_optional(annotation)
            )
            stored[column.key] = StoredField(annotation, nullable)

        relations: dict[str, FieldMeta] = {}
        targets: dict[str, type] = {}
        declared = mapping_registry.relations_of(entity)
        for name, annotation in annotations.items():
            if name in stored:
                continue
            related, many = related_class(annotation)
            relation = declared.get(name)
            if relation is None and (related is None or not is_mapped(related)):
                continue
            related_name = relation.related.__name__ if relation is not None else related.__name__  # type: ignore[union-attr]
            logical = _relational_type(relation.kind if relation else None, many)
            identified_by = relation.identified_by if relation is not None else None
            # The pair is published expanded, whichever way it was declared, so a client reads how
            # the two sides meet without knowing the cardinality convention `identified_by` obeys.
            here, there = (
                key_pair(identity, relation, many) if relation is not None else (None, None)
            )
            nullable = logical is FieldType.MANY2ONE
            if nullable and here is not None and here in mapper.columns:
                nullable = bool(mapper.columns[here].nullable)
            relations[name] = FieldMeta.for_relation(
                logical,
                related_name,
                identified_by,
                nullable,
                parent_field=here,
                related_field=there,
                navigable=relation is None
                or relation.kind in (RelationKind.FOREIGN_KEY, RelationKind.MANY_TO_MANY),
                reference=_is_reference(relation),
            )
            if relation is not None:
                targets[name] = relation.related
            elif related is not None:
                targets[name] = related

        extended: dict[str, FieldMeta] = {}
        for extension in extensions.under(entity):
            added = ModelIntrospection.describe(extension.entity).fields
            for name in extension.fields:
                if (
                    name in stored
                    or name in relations
                    or name in extended
                    or name not in added
                ):
                    continue
                own = added[name]
                absent = (
                    FieldMeta.for_column(
                        own.type,
                        True,
                        own.choices,
                        own.exact,
                        choice_labels=own.choice_labels,
                    )
                    if own.relation is None and own.type is not FieldType.EMBEDDED
                    else own
                )
                extended[name] = absent.model_copy(
                    update={"owner": extension.entity.__name__}
                )

        def pointed_at(name: str) -> Meta | None:
            """The description of what the relation `name` points at, as this store holds it."""
            return ModelIntrospection.describe(targets[name]) if name in targets else None

        return StoreFacts(
            identity=identity,
            stored=stored,
            relations=relations,
            extended=extended,
            related=pointed_at,
        )

    @staticmethod
    @cache
    def describe(entity: type) -> Meta:
        """What a caller is told about this aggregate: the metamodel's description, with what
        the mapper knows (`facts`), and a key other than the identity checked unique on disk.

            in      the class Dataset, mapped to the `dataset` table
            out     Meta(aggregate='Dataset', identity='dataset_id', fields={...})

        >>> ModelIntrospection.describe(Dataset).identity
        'dataset_id'
        >>> ModelIntrospection.describe(Dataset).fields["column_names"].type    # Text on disk
        <FieldType.TEXT_LIST: 'text[]'>
        """
        facts = ModelIntrospection.facts(entity)
        meta = description_of(entity, facts)
        _key_is_unique(entity, inspect(entity).local_table, meta.get_id, facts.identity)
        return meta

    @staticmethod
    def describe_statement(
        statement: TextualSelect, model: type | None = None, identity: str = ""
    ) -> Meta:
        """What a statement the caller wrote returns, described the way a model is, so a
        criteria over it is read exactly as `search` reads one: each value as its field's type,
        what it cannot answer dropped and reported.

            in      text("SELECT t.*, l.label FROM thing t JOIN …").columns(*thing.c, column("label", Text)),
                    Thing
            out     Thing's Meta, and `label` as a nullable text beside its fields
            in      text("SELECT o.id, o.total FROM …").columns(column("id", Text), column("total", Numeric)),
                    identity="id"
            out     Meta(aggregate="statement", identity="id", default_order="-id",
                         fields={"id": text, "total": decimal})

        1. Read as a model: the model's definition, and every column the model maps has to come
           back under its name — a row read as the model needs each of them.
        2. Read as rows: the identity names the column that tells two rows apart, so it has to
           be one of them.
        3. Each other column, as its type says: a column typed by `.columns()` is filtered as that
           type; an untyped one is ordered and shown but takes no condition. A column that does
           not say it is NOT NULL may hold NULL.
        Final: the model's definition with those columns beside its fields, or a definition of
        the rows alone ordered newest first by the identity.
        """
        returned = statement.selected_columns
        mapped = (
            [column.name for column in inspect(model).persist_selectable.columns]
            if model is not None
            else []
        )
        if model is not None:
            missing = [name for name in mapped if name not in returned]
            if missing:
                raise exceptions.ProgrammingError(
                    f"the statement does not return {', '.join(missing)}: a row read as "
                    f"{model.__name__} needs every column the model maps, under its name"
                )
        elif identity not in returned:
            raise exceptions.ProgrammingError(
                f"the statement does not return its identity '{identity}': a page of rows is "
                "ordered and resumed by the column that tells two rows apart"
            )

        base = ModelIntrospection.describe(model) if model is not None else None
        held = base.fields if base is not None else {}
        columns = {
            name: FieldMeta.for_column(
                logical_type(_column_type(column)), bool(getattr(column, "nullable", True))
            )
            for name, column in returned.items()
            if name not in held and name not in mapped
        }
        if base is not None:
            return base.model_copy(update={"fields": {**columns, **held}})
        return Meta(
            aggregate="statement",
            identity=identity,
            default_order=f"-{identity}",
            fields=columns,
            name=MultiLanguage("statement"),
        )
