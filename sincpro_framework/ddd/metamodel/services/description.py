"""The description a client and a criteria are checked against, built from one model — the same
function whichever store answers, each store adding only what it knows.

    described(model_of(Sale), StoreFacts(identity="id"))           memory
    described(model_of(Sale), facts_of_the_mapper)                 SQL: columns and relations
    described(model_of(Size), StoreFacts(identity=""))             a value object: no identity

Context: what a field is called, what it may hold, its hints and its total come from the model,
here, once; a store answers only where a row keeps it, whether it may be empty and how a relation
meets. So two stores cannot describe one entity two ways.
"""

import dataclasses
from decimal import Decimal
from typing import Any

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.entity.entity import Entity
from sincpro_framework.ddd.entity.entity_id import key_of
from sincpro_framework.ddd.entity.entity_meta import (
    FieldMeta,
    FieldType,
    Meta,
    choice_translations_of,
    logical_type,
    members_of,
    related_class,
    without_optional,
)
from sincpro_framework.ddd.entity.presentation import Widget
from sincpro_framework.ddd.metamodel.domain.facts import StoredField, StoreFacts
from sincpro_framework.ddd.metamodel.domain.model import Model
from sincpro_framework.ddd.metamodel.services.compiler import compiled, declaration_of
from sincpro_framework.ddd.money import Money
from sincpro_framework.ddd.multi_currency import MultiCurrency
from sincpro_framework.ddd.multi_language import MultiLanguage
from sincpro_framework.ddd.value_object import holds_of

EVENT_ENVELOPE = frozenset({"entity_type", "entity_id", "correlation_id", "causation_id"})
"""The fields that make a class a domain event's: one carrying them also answers its `name`."""


def _shape_of(annotation: Any) -> tuple[type | None, bool]:
    """The value object an annotation holds inside the record, and whether it holds several:
    a dataclass or a pydantic model with no identity of its own. An entity is a relation, never
    a shape.

        _shape_of(Size | None)        ->  (Size, False)
        _shape_of(list[Size])         ->  (Size, True)
        _shape_of(Writer | None)      ->  (None, False)    an entity: a relation
        _shape_of(str)                ->  (None, False)
    """
    kind, many = related_class(annotation)
    if kind is None or issubclass(kind, Entity):
        return None, False
    if dataclasses.is_dataclass(kind) or hasattr(kind, "model_fields"):
        return kind, many
    return None, False


def _column(annotation: Any, nullable: bool) -> FieldMeta:
    """A field kept as a value of its own type: text, a number, a date, a choice, a list of
    texts — with what its value object holds, when it says, and whose record it names, when it
    is a key (`EntityID[Project]`: a text whose `relation` is `Project`)."""
    column = FieldMeta.for_column(
        logical_type(annotation),
        nullable,
        members_of(annotation),
        isinstance(without_optional(annotation), type)
        and issubclass(without_optional(annotation), Decimal),
        choice_labels=choice_translations_of(annotation),
    )
    said: dict[str, Any] = {}
    held = holds_of(without_optional(annotation))
    if held is not None:
        said["holds"] = held
    key = key_of(annotation)
    if key is not None and key.relation is not None:
        said["relation"] = key.relation
    return column.model_copy(update=said) if said else column


def _fields(model: Model, facts: StoreFacts) -> dict[str, FieldMeta]:
    """Every field the model and the store hold, each as what it keeps: a value, or a value object
    inside the record, described by its own model.

    1. The store's own fields, in its order, when it lists them; the annotations otherwise, each
       empty when its annotation allows `None`.
    2. A value object inside the record is embedded, carrying its shape — an amount (`Money`,
       `MultiCurrency`) shown as money.
    3. A field the store keeps is described whatever it holds — a JSON slot too; one read off the
       annotations that is neither a value nor a shape — another entity — is the store's to
       describe, as a relation.
    Final: the fields, in order.
    """
    stored = facts.stored
    kept_by_the_store = stored is not None
    if stored is None:
        stored = {
            name: StoredField(annotation, annotation != without_optional(annotation))
            for name, annotation in model.annotations.items()
        }
    fields: dict[str, FieldMeta] = {}
    for name, held in stored.items():
        shape, many = _shape_of(held.annotation)
        if shape is not None:
            fields[name] = FieldMeta.for_embedded(
                described(compiled(declaration_of(shape)), StoreFacts(identity="")),
                many,
                held.nullable,
            )
            if issubclass(shape, (Money, MultiCurrency)):
                fields[name] = fields[name].model_copy(update={"widget": Widget.MONEY})
        elif (
            kept_by_the_store
            or related_class(held.annotation)[0] is None
            or logical_type(held.annotation) is not FieldType.UNKNOWN
        ):
            fields[name] = _column(held.annotation, held.nullable)
    return fields


WIDGET_OF = {
    FieldType.TEXT: Widget.TEXT,
    FieldType.INTEGER: Widget.NUMBER,
    FieldType.NUMBER: Widget.NUMBER,
    FieldType.BOOLEAN: Widget.CHECKBOX,
    FieldType.DATE: Widget.DATE,
    FieldType.DATETIME: Widget.DATETIME,
    FieldType.TEXT_LIST: Widget.TAGS,
    FieldType.TRANSLATED: Widget.TRANSLATED,
    FieldType.EMBEDDED: Widget.EMBEDDED,
    FieldType.MANY2ONE: Widget.MANY2ONE,
    FieldType.ONE2MANY: Widget.LINES,
    FieldType.MANY2MANY: Widget.MANY2MANY,
    FieldType.UUID: Widget.IDENTIFIER,
}
"""The widget each type is shown with when nothing else is said."""


def held_relations(model: Model, navigable: bool) -> dict[str, FieldMeta]:
    """Each field of `model` that holds another entity, as a relation: a list of them a
    one-to-many, one of them a many-to-one. What a store holding the records themselves says
    (navigable: a filter may cross it), and what is published of an entity no store described
    yet (not navigable: no store said a filter may cross it).

        held_relations(model_of(Sale), True)
            ->  {"customer": FieldMeta(type=many2one, relation="Customer", navigable=True), …}
    """
    relations: dict[str, FieldMeta] = {}
    for name, annotation in model.annotations.items():
        kind, many = related_class(annotation)
        if isinstance(kind, type) and issubclass(kind, Entity):
            relations[name] = FieldMeta.for_relation(
                FieldType.ONE2MANY if many else FieldType.MANY2ONE,
                kind.__name__,
                None,
                nullable=not many,
                navigable=navigable,
            )
    return relations


def _widget_of(meta: FieldMeta) -> str:
    """The widget a field's type gives: the one its value already names (an amount, money), a
    select for a choice, a decimal input for an exact number, lines for a list of records, the
    type's own otherwise — a text when the type says nothing."""
    if meta.widget:
        return meta.widget
    if meta.choices:
        return Widget.SELECT
    if meta.type is FieldType.NUMBER and meta.exact:
        return Widget.DECIMAL
    if meta.type is FieldType.EMBEDDED and meta.many:
        return Widget.LINES
    return WIDGET_OF.get(meta.type, Widget.TEXT)


def _hinted(fields: dict[str, FieldMeta], model: Model) -> dict[str, FieldMeta]:
    """Each field with what the model says of it for a screen: its label and help, computed or
    not, read-only, required, its default, the three conditions, its total, its widget — the
    one `presentation` names, else its type's (`_widget_of`) —, what a change of it reaches
    (`affects`, `judged_by`) and, for a computed one the language writes down, its formula
    (`computes`).

    1. A total the field's type cannot be folded with is refused, naming what it takes.
    2. A field a database computes is marked computed and has no default.
    Final: each field with its words and hints.
    """
    presented = model.presented
    for name, function in presented.totals.items():
        meta = fields.get(name)
        if meta is not None and function not in meta.aggregates:
            offered = [one for one in meta.aggregates if one.value != "percentile"]
            raise exceptions.ProgrammingError(
                f"{model.name}.presentation adds up {name} by {function}; "
                f"a {meta.type} adds up by {', '.join(offered) or 'nothing'}"
            )
    hinted: dict[str, FieldMeta] = {}
    for name, meta in fields.items():
        computed = meta.computed or name in model.computed
        hinted[name] = meta.model_copy(
            update={
                "label": (
                    MultiLanguage(model.labels[name]) if name in model.labels else meta.label
                ),
                "help": (
                    MultiLanguage(model.helps[name]) if name in model.helps else meta.help
                ),
                "computed": computed,
                "readonly": name in presented.readonly,
                "required": name in presented.required,
                "default": None if computed else model.defaults.get(name),
                "readonly_when": presented.readonly_when.get(name),
                "required_when": presented.required_when.get(name),
                "visible_when": presented.visible_when.get(name),
                "total": presented.totals.get(name),
                "widget": presented.widgets.get(name) or _widget_of(meta),
                "affects": list(model.affects.get(name, ())),
                "judged_by": list(model.judged_by.get(name, ())),
                "computes": model.formulas.get(name),
            }
        )
    return hinted


def _overlaid(fields: dict[str, FieldMeta], model: Model) -> dict[str, FieldMeta]:
    """The fields with what an extension changed of the entity's own — a label, a hint —, as
    the model holds it: merged from the base class down, so the deepest extension wins. A label
    or a help is read as the text in every language it is, as one the class declares."""
    for name, changes in model.overlays.items():
        if name in fields:
            fields[name] = fields[name].model_copy(
                update={
                    key: MultiLanguage(value) if key in ("label", "help") else value
                    for key, value in changes.items()
                }
            )
    return fields


def described(model: Model, facts: StoreFacts) -> Meta:
    """The description of `model` as a store holding it answers it.

        described(model_of(Sale), StoreFacts(identity="id"))
            ->  Meta(aggregate="Sale", identity="id", fields={…}, default_order="-id", …)

    1. The fields the model and the store hold (`_fields`); a domain event also answers its
       `name`, a class attribute every row keeps.
    2. The store's relations and the fields its extensions add, beside them.
    3. The words and hints of the model (`_hinted`), then what extensions overlay (`_overlaid`).
    Final: the description, with how the entity is read — its key, its order, its display, its
    search and its detail —, able to check a filter that crosses a relation when the store says
    how to describe what it points at.
    """
    fields = _fields(model, facts)
    if EVENT_ENVELOPE <= set(model.annotations) and isinstance(
        getattr(model.entity, "name", None), str
    ):
        fields.setdefault("name", FieldMeta.for_column(FieldType.TEXT, False, [], False))
    for name, relation in facts.relations.items():
        fields.setdefault(name, relation)
    for name, added in facts.extended.items():
        fields.setdefault(name, added)
    fields = _overlaid(_hinted(fields, model), model)
    presented = model.presented
    order = ",".join(str(sort) for sort in presented.order) or (
        f"-{facts.identity}" if facts.identity else ""
    )
    described_meta = Meta(
        aggregate=model.name,
        identity=facts.identity,
        default_order=order,
        get_id=presented.get_id,
        display=presented.display,
        search=presented.search,
        detail=presented.detail,
        fields=fields,
        name=MultiLanguage(dict(model.title)),
    )
    return (
        described_meta.with_related(facts.related)
        if facts.related is not None
        else described_meta
    )
