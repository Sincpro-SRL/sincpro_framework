"""The published contract of an entity: one JSON Schema 2020-12 object, with what the framework
says beyond JSON Schema under one `x-sincpro` object per schema and per property.

    rendered(description_of(Sale, facts), fingerprint)
        ->  {"type": "object", "title": "Sale",
             "properties": {"total": {"type": "string", "format": "decimal",
                                      "x-sincpro": {"type": "number", "widget": "decimal", …}}, …},
             "required": [...],
             "x-sincpro": {"entity": "Sale", "identity": "id", "version": "3f9c…", …}}

Context: OpenAPI 3.1 is this dialect, MCP describes tools in it and any validator reads its
keywords, so the value of a field is checked by any client with the tools it already has; what a
form and a filter need on top — the kind, the widget, the hints and their conditions, how a
relation meets, what a change affects — travels beside it, derived, never declared twice. A
relation points at the related entity's schema (`#/$defs/Customer`), as pydantic writes it.
"""

import dataclasses
import typing
from typing import Any, get_args

from pydantic import BaseModel

from sincpro_framework.ddd.entity.entity import Entity
from sincpro_framework.ddd.entity.entity_meta import FieldMeta, FieldType, Meta
from sincpro_framework.ddd.value_object import keywords_of

PRIMITIVE: dict[FieldType, dict[str, Any]] = {
    FieldType.TEXT: {"type": "string"},
    FieldType.INTEGER: {"type": "integer"},
    FieldType.NUMBER: {"type": "number"},
    FieldType.BOOLEAN: {"type": "boolean"},
    FieldType.DATE: {"type": "string", "format": "date"},
    FieldType.DATETIME: {"type": "string", "format": "date-time"},
    FieldType.UUID: {"type": "string", "format": "uuid"},
    FieldType.TEXT_LIST: {"type": "array", "items": {"type": "string"}},
    FieldType.TRANSLATED: {"type": "object", "additionalProperties": {"type": "string"}},
}
"""What each kind of field holds, as JSON Schema says it."""

SAID = (
    "type",
    "widget",
    "label",
    "help",
    "readonly",
    "default",
    "readonly_when",
    "required_when",
    "visible_when",
    "total",
    "sortable",
    "groupable",
    "aggregates",
    "ops",
    "computed",
    "owner",
    "choice_labels",
    "exact",
    "relation",
    "many",
    "identified_by",
    "parent_field",
    "related_field",
    "navigable",
    "holds",
    "computes",
    "affects",
    "judged_by",
)
"""What a property's `x-sincpro` may say, in this order; what says nothing is left out."""


def _held(field: FieldMeta) -> dict[str, Any]:
    """What the field holds, as JSON Schema: its primitive, its choices, the keywords of what its
    value object holds, a shape inside the record, or the schema of the entity a relation
    points at — empty when its value is not described.

    An exact number travels as a text (`"12.50"`), so no numeric keyword is written on it — a
    validator would skip `minimum` on a string, silently; what it holds travels whole under
    `x-sincpro.holds`, the one check that reads it."""
    if field.type is FieldType.EMBEDDED and field.definition is not None:
        shape = rendered(field.definition)
        return {"type": "array", "items": shape} if field.many else shape
    if field.relation is not None:
        pointed = {"$ref": f"#/$defs/{field.relation}"}
        return {"type": "array", "items": pointed} if field.many else pointed
    if field.type is FieldType.NUMBER and field.exact:
        return {"type": "string", "format": "decimal"}
    held: dict[str, Any] = dict(PRIMITIVE.get(field.type, {}))
    if field.choices:
        held["enum"] = list(field.choices)
    if field.holds is not None:
        held.update(keywords_of(field.holds))
    return held


def _said(field: FieldMeta) -> dict[str, Any]:
    """What the framework says of the field beyond JSON Schema, as JSON, leaving out what says
    nothing — an empty text, a `False`, an empty list, a `None`."""
    dumped = field.model_dump(mode="json")
    said: dict[str, Any] = {"type": dumped["type"]}
    for name in SAID[1:]:
        value = dumped.get(name)
        if value in (None, False, "", [], {}):
            continue
        said[name] = value
    return said


def _property(field: FieldMeta) -> dict[str, Any]:
    """One property: what it holds, possibly empty when nullable, its title, and `x-sincpro`."""
    held: dict[str, Any] = _held(field)
    if field.nullable and held:
        held = {"anyOf": [held, {"type": "null"}]}
    title = field.label.in_() if field.label else ""
    if title:
        held["title"] = title
    held["x-sincpro"] = _said(field)
    return held


def rendered(meta: Meta, version: str = "") -> dict[str, Any]:
    """The JSON Schema of a record the description `meta` describes, with its `x-sincpro`.

        rendered(description_of(Size))
            ->  {"type": "object", "title": "Size", "properties": {"width": …}, "required": […],
                 "x-sincpro": {"entity": "Size"}}

    1. Each field as a property (`_property`): what it holds, its title, what the framework says.
    2. Required: the fields a form must fill — the hint `required`.
    3. The schema's own `x-sincpro`: the entity, how it is read — its identity, its key, its
       display, its order, its search and its detail —, its title in every language, and the
       version of its model, when given.
    Final: the schema, JSON all through.
    """
    dumped = meta.model_dump(mode="json")
    entity: dict[str, Any] = {"entity": meta.aggregate}
    for name in ("identity", "get_id", "display", "default_order", "search", "detail"):
        if dumped.get(name):
            entity[name] = dumped[name]
    entity["title"] = dumped["name"]
    if version:
        entity["version"] = version
    return {
        "type": "object",
        "title": meta.name.in_() or meta.aggregate,
        "properties": {name: _property(field) for name, field in meta.fields.items()},
        "required": [name for name, field in meta.fields.items() if field.required],
        "x-sincpro": entity,
    }


def entities_in(annotation: Any, seen: set[Any]) -> list[type]:
    """Every entity `annotation` reaches — itself, a field of a DTO or a dataclass, an argument of
    a generic —, each once, in the order met; `seen` holds every type already walked, so a model
    that reaches itself is walked once.

        entities_in(ResponseSales, set())     ->  [Sale]           # sales: list[Sale]
        entities_in(int, set())               ->  []
    """
    reached: list[type] = []
    for argument in get_args(annotation):
        reached += entities_in(argument, seen)
    if not isinstance(annotation, type) or annotation in seen:
        return reached
    seen.add(annotation)
    if issubclass(annotation, Entity):
        return [*reached, annotation]
    if issubclass(annotation, BaseModel):
        for field in annotation.model_fields.values():
            reached += entities_in(field.annotation, seen)
    elif dataclasses.is_dataclass(annotation):
        try:
            hints = typing.get_type_hints(annotation)
        except Exception:
            hints = {}
        for hint in hints.values():
            reached += entities_in(hint, seen)
    return reached
