"""The metamodel compiler: a declaration in, a model out.

    declaration_of(Sale)              what the class declares, read once
    compiled(declaration_of(Sale))    the model every reader takes its part from

Context: this is the one place that reads what an entity declares. Everything else — a
repository, a preview, a save, the description a client gets — asks for the model.
"""

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from typing import Any, cast

from sincpro_framework.ddd.criteria import Formulas
from sincpro_framework.ddd.entity import extensions
from sincpro_framework.ddd.entity.computed import computed_fields_of
from sincpro_framework.ddd.entity.entity_meta import (
    annotations_of,
    checks_of,
    computations_of,
    defaults_of,
    field_translations,
    framework_fields,
    presentation_of,
    related_class,
)
from sincpro_framework.ddd.metamodel.domain.declaration import Declaration
from sincpro_framework.ddd.metamodel.domain.model import Model
from sincpro_framework.ddd.multi_language import MultiLanguage


def declaration_of(declared: type) -> Declaration:
    """What `declared` says about itself, each part read off the class once.

        declaration_of(Sale)    ->  Declaration(name="Sale", computations=(…), checks=(…), …)
        declaration_of(Money)   ->  a value object: no computations, no checks, no hints

    A class that is not a dataclass declares its annotations alone; its parts are empty. What
    the modules extending it changed about how a field is shown (`extensions.overlay`) is read
    from the base class down, so the deepest extension wins.
    """
    record = dataclasses.is_dataclass(declared)
    overlays: dict[str, dict[str, Any]] = {}
    for ancestor in reversed(declared.__mro__):
        for name, changes in extensions.OVERLAYS.get(ancestor, {}).items():
            overlays.setdefault(name, {}).update(changes)
    translator = getattr(declared, "translations", None)
    title = (
        dict(MultiLanguage(cast(Mapping[str, str], translator())))
        if callable(translator)
        else {"default": declared.__name__}
    )
    return Declaration(
        entity=declared,
        name=declared.__name__,
        title=title,
        annotations=annotations_of(declared),
        framework_fields=framework_fields(declared),
        computations=computations_of(declared) if record else (),
        checks=checks_of(declared) if record else (),
        computed=computed_fields_of(declared),
        presented=presentation_of(declared),
        defaults=defaults_of(declared),
        labels={
            name: dict(text) for name, text in field_translations(declared, "label").items()
        },
        helps={
            name: dict(text) for name, text in field_translations(declared, "help").items()
        },
        overlays=overlays,
    )


def _owned(declaration: Declaration) -> tuple[str, ...]:
    """The fields holding records a pass over the entity runs first: those its computations read
    or its checks watch, and the lists of records that compute or check something themselves.
    Never another class that declares nothing.

        Sale(lines: list[SaleLine])     ->  ("lines",)   SaleLine computes its amount
        Box(items: list[Item])          ->  ()           neither computes nor checks

    Context: a single record field counts only when a computation reads it or a check watches it;
    a list counts also when its records compute or check something.
    """
    read = {name for one in declaration.computations for name in one.depends} | {
        name for one in declaration.checks for name in one.watches
    }
    found: list[str] = []
    for name, annotation in declaration.annotations.items():
        kind, many = related_class(annotation)
        if kind is None or not dataclasses.is_dataclass(kind):
            continue
        if name in read or (many and (computations_of(kind) or checks_of(kind))):
            found.append(name)
    return tuple(found)


def _reach(
    declaration: Declaration,
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """For each field, the computed fields a change of it reaches, in the order they run, and
    the checks that judge it or what it reaches.

        Sale: total ← lines; payment_term ← customer (editable); check delivery ← [delivery, state]
        ->  affects   {"lines": ("total",), "customer": ("payment_term",)}
            judged_by {"delivery": ("delivery_when_confirmed",), "state": (…)}

    1. A field reaches itself.
    2. Each computation, in order, that reads a reached field is reached too, so a computation of
       a computation follows.
    3. A check judges the field when it watches the field or anything it reached.
    Final: only the fields that reach something are listed.
    """
    affects: dict[str, tuple[str, ...]] = {}
    judged_by: dict[str, tuple[str, ...]] = {}
    for name in declaration.annotations:
        reached = {name}
        computed: list[str] = []
        for computation in declaration.computations:
            if not reached.isdisjoint(computation.depends):
                reached.add(computation.field)
                computed.append(computation.field)
        checks = [
            one.method for one in declaration.checks if not reached.isdisjoint(one.watches)
        ]
        if computed:
            affects[name] = tuple(computed)
        if checks:
            judged_by[name] = tuple(checks)
    return affects, judged_by


def _formulas(declaration: Declaration) -> dict[str, Any]:
    """For each computed field whose method returns what the language writes down, its formula
    (`Formulas.of_a_method`); a computation that does anything else has none.

        Line(amount ← qty * price)          ->  {"amount": {"op": "*", "args": [qty, price]}}
        Sale(discount ← a price list read)  ->  {}
    """
    found: dict[str, Any] = {}
    for one in declaration.computations:
        formula = Formulas.of_a_method(getattr(declaration.entity, one.method))
        if formula is not None:
            found[one.field] = formula
    return found


def _fingerprint(declaration: Declaration) -> str:
    """The declaration's content hashed — the same text wherever the same code runs, another one
    the moment anything it declares changes.

    Context: what is hashed is what a reader can see — names, types, what is computed from what,
    what is checked, the hints and their conditions, the defaults, the words, what an extension
    overlays, the formulas a client runs; a method's body is not otherwise, since a client never
    runs it.
    """
    presented = declaration.presented
    content: Mapping[str, Any] = {
        "name": declaration.name,
        "title": declaration.title,
        "annotations": {name: str(one) for name, one in declaration.annotations.items()},
        "framework_fields": sorted(declaration.framework_fields),
        "computations": [
            [one.field, list(one.depends), one.method, one.editable]
            for one in declaration.computations
        ],
        "checks": [[list(one.watches), one.method] for one in declaration.checks],
        "computed": sorted(declaration.computed),
        "presented": {
            "get_id": presented.get_id,
            "display": presented.display,
            "search": presented.search.model_dump(mode="json") if presented.search else None,
            "order": [str(one) for one in presented.order],
            "detail": presented.detail.model_dump(mode="json") if presented.detail else None,
            "readonly": sorted(presented.readonly),
            "required": sorted(presented.required),
            "readonly_when": {
                k: v.model_dump(mode="json") for k, v in presented.readonly_when.items()
            },
            "required_when": {
                k: v.model_dump(mode="json") for k, v in presented.required_when.items()
            },
            "visible_when": {
                k: v.model_dump(mode="json") for k, v in presented.visible_when.items()
            },
            "totals": {k: str(v) for k, v in presented.totals.items()},
        },
        "defaults": declaration.defaults,
        "labels": declaration.labels,
        "helps": declaration.helps,
        "overlays": declaration.overlays,
        "formulas": _formulas(declaration),
    }
    text = json.dumps(content, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def compiled(declaration: Declaration) -> Model:
    """The model of `declaration`: what it declares, and what follows from it.

        compiled(declaration_of(Sale))
            ->  Model(name="Sale", owned=("lines",), affects={"lines": ("total",)}, …)

    1. What it declares is carried as the declaration says it.
    2. The fields a pass runs first (`_owned`).
    3. What each field's change reaches and what judges it (`_reach`).
    4. The formulas of the computations the language writes down (`_formulas`).
    Final: the model, with its fingerprint.
    """
    affects, judged_by = _reach(declaration)
    return Model(
        declaration=declaration,
        entity=declaration.entity,
        name=declaration.name,
        title=declaration.title,
        annotations=declaration.annotations,
        framework_fields=declaration.framework_fields,
        computations=declaration.computations,
        checks=declaration.checks,
        computed=declaration.computed,
        presented=declaration.presented,
        defaults=declaration.defaults,
        labels=declaration.labels,
        helps=declaration.helps,
        overlays=declaration.overlays,
        owned=_owned(declaration),
        formulas=_formulas(declaration),
        affects=affects,
        judged_by=judged_by,
        fingerprint=_fingerprint(declaration),
    )
