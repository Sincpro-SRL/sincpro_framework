"""Reads a mapped class into the definition a caller is told about.

The one place this layer asks SQLAlchemy what a class looks like. `Meta` itself is pure
vocabulary; this is the half that knows there is a mapper behind it — which columns exist,
which may be NULL, which annotation points at another mapped class.

    describe(Dataset)   →   Meta(aggregate='Dataset', identity='dataset_id', fields={…})

Cached, because mappings are applied once at boot and a table grows no column at runtime.
"""

from dataclasses import is_dataclass
from functools import cache
from typing import Any, TypeGuard, cast

from sqlalchemy import inspect
from sqlalchemy.exc import NoInspectionAvailable

from sincpro_framework.ddd.entity import Translated
from sincpro_framework.ddd.entity.model_meta import (
    FieldMeta,
    FieldType,
    Meta,
    annotations_of,
    describe_class,
    enum_of,
    field_translations,
    logical_type,
    related_class,
)
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.orm.sqlalchemy.data_mapper import relations_of


def _is_shape(candidate: Any) -> bool:
    """A dataclass or a pydantic model that is not mapped: a value object with a shape."""
    if not isinstance(candidate, type) or is_mapped(candidate):
        return False
    return is_dataclass(candidate) or hasattr(candidate, "model_fields")


def describe_shape(value_object: type) -> Meta:
    """The definition of a value object embedded in a row: its annotations, no table, and no
    identity of its own, so nothing survives a mask on its own account.

    in      Shape(width: int, height: int, unit: str = "cm")
    out     Meta(aggregate='Shape', identity='', fields={width, height, unit})
    """
    return describe_class(value_object)


def _relational_type(kind: str, many: bool) -> FieldType:
    if kind == "many_to_many":
        return FieldType.MANY2MANY
    return FieldType.ONE2MANY if many else FieldType.MANY2ONE


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
    3. A column whose annotation is a value object, a dataclass or a pydantic model with no
       table, is an embedded field carrying that shape as its definition.
    4. Final: any other annotation pointing at a mapped class, or any relation the data mapper
       declared or inferred, is a relational field: `many2one`, `one2many` or `many2many`,
       with the aggregate on the other side and what identifies the relation.

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

    annotations = annotations_of(entity)
    identity = mapper.primary_key[0].name
    translator = getattr(entity, "translations", None)
    aggregate_name: Translated = (
        cast(Translated, translator())
        if callable(translator)
        else {"default": entity.__name__}
    )

    fields: dict[str, FieldMeta] = {}
    for column in mapper.columns:
        annotation = annotations.get(column.key, Any)
        shape, many = related_class(annotation)
        if shape is not None and _is_shape(shape):
            fields[column.key] = FieldMeta.for_embedded(
                describe_shape(shape), many, bool(column.nullable)
            )
        else:
            fields[column.key] = FieldMeta.for_column(
                logical_type(annotation), bool(column.nullable), _members_of(annotation)
            )

    declared = relations_of(entity)
    for name, annotation in annotations.items():
        if name in fields:
            continue
        related, many = related_class(annotation)
        relation = declared.get(name)
        if relation is None and (related is None or not is_mapped(related)):
            continue
        related_name = relation.related.__name__ if relation is not None else related.__name__  # type: ignore[union-attr]
        logical = _relational_type(relation.kind if relation else "", many)
        identified_by = relation.identified_by if relation is not None else None
        nullable = logical is FieldType.MANY2ONE
        if nullable and identified_by is not None and identified_by in mapper.columns:
            nullable = bool(mapper.columns[identified_by].nullable)
        fields[name] = FieldMeta.for_relation(logical, related_name, identified_by, nullable)

    labels, helps = field_translations(entity, "label"), field_translations(entity, "help")
    if labels or helps:
        fields = {
            name: meta.model_copy(
                update={"label": labels.get(name, {}), "help": helps.get(name, {})}
            )
            for name, meta in fields.items()
        }

    return Meta(
        aggregate=entity.__name__,
        identity=identity,
        default_order=f"-{identity}",
        fields=fields,
        name=aggregate_name,
    )
