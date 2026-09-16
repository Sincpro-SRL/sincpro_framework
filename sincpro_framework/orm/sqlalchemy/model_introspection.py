"""Reads a mapped class into the definition a caller is told about.

The one place this layer asks SQLAlchemy what a class looks like. `Meta` itself is pure
vocabulary; this is the half that knows there is a mapper behind it — which columns exist,
which may be NULL, which annotation points at another mapped class.

    describe(Dataset)   →   Meta(aggregate='Dataset', identity='dataset_id', fields={…})

Cached, because mappings are applied once at boot and a table grows no column at runtime.
"""

from functools import cache
from typing import Any, TypeGuard, cast, get_args, get_origin, get_type_hints

from sqlalchemy import inspect
from sqlalchemy.exc import NoInspectionAvailable

from sincpro_framework.ddd.entity import Translated
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.model_meta import (
    FieldMeta,
    Meta,
    RelationMeta,
    enum_of,
    logical_type,
    without_optional,
)


def _relation(annotation: Any) -> RelationMeta | None:
    """A relation, if this annotation points at another mapped class.

        in  list[Run]    →  out  RelationMeta(target='Run', many=True)
        in  Shelf | None →  out  RelationMeta(target='Shelf', many=False)
        in  str          →  out  None                          not a mapped class

    The annotation is the whole declaration: nobody writes a cardinality.
    """
    base = without_optional(annotation)
    many = get_origin(base) is list
    if many:
        members = get_args(base)
        base = members[0] if members else None

    if not is_mapped(base):
        return None
    return RelationMeta(target=base.__name__, many=many)


def _annotations_of(entity: type) -> dict[str, Any]:
    """The class's annotations with forward references resolved.

        in  Dataset  →  out  {'dataset_id': str, 'row_count': int, …}

    A reference that does not resolve fails here, when the aggregate is described at boot,
    rather than on the first request that asks for it.
    """
    try:
        return get_type_hints(entity)
    except NameError as error:
        raise ContractViolation(
            f"{entity.__name__} names a type that cannot be resolved at runtime: {error}"
        ) from error


def _members_of(annotation: Any) -> list[Any]:
    """The values an enum field may hold, in declaration order.

    in  Stage | None   →  out  ['started', 'fitted', 'generated']
    in  str            →  out  []
    """
    members = enum_of(annotation)
    return [] if members is None else [member.value for member in members]


def is_mapped(candidate: Any) -> TypeGuard[type]:
    """Whether the ORM maps this class.

    in  Dataset  →  True        it has a table
    in  str      →  False       a plain value
    """
    if not isinstance(candidate, type):
        return False
    try:
        inspect(candidate)
        return True
    except NoInspectionAvailable:
        return False


@cache
def describe(entity: type) -> Meta:
    """Context: what a caller is told about this aggregate, read off the model itself.

        in      the class Dataset, mapped to the `dataset` table
        read    annotations for logical types, columns for nullability
        out     Meta(aggregate='Dataset', identity='dataset_id', fields={...})

    1. Inspect the mapper; refuse a class with no table, since it has no fields to ask about.
    2. Read the annotations for logical types, the columns for nullability, and carry the
       class's `translations()` as it answered it.
    3. Final: any remaining annotation pointing at a mapped class is a relation.

    >>> describe(Dataset).identity
    'dataset_id'
    >>> describe(Dataset).fields["column_names"].type    # list[str] in the model, Text on disk
    <FieldType.TEXT_LIST: 'text[]'>
    """
    try:
        mapper = inspect(entity)
    except NoInspectionAvailable as error:
        raise ContractViolation(
            f"{entity.__name__} is not a mapped aggregate; there is nothing to query"
        ) from error

    annotations = _annotations_of(entity)
    identity = mapper.primary_key[0].name
    translator = getattr(entity, "translations", None)
    words: Translated = (
        cast(Translated, translator())
        if callable(translator)
        else {"name": {"default": entity.__name__}, "labels": {}}
    )

    fields = {
        column.key: FieldMeta.for_column(
            logical_type(annotations.get(column.key, Any)),
            bool(column.nullable),
            _members_of(annotations.get(column.key, Any)),
        )
        for column in mapper.columns
    }

    relations = {
        name: relation
        for name, annotation in annotations.items()
        if name not in fields and (relation := _relation(annotation)) is not None
    }

    return Meta(
        aggregate=entity.__name__,
        identity=identity,
        default_order=f"-{identity}",
        fields=fields,
        relations=relations,
        translations=words,
    )
