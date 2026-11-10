"""The metamodel of a bus: every model its operations reach, each as its published contract, with
the operations a client calls on each, under one version — what a build writes to a file for a
client that works offline, generates its types and its client from, or ships as fixtures.

    metamodel_of(billing)
        ->  {"bounded_context": "billing",
             "version": "9b1c…",
             "models": {"Invoice": {"type": "object", "x-sincpro": {…}, …}, "Partner": {…}},
             "operations": {
                 "billing.QueryGetInvoice": {
                     "role": "get", "query": True, "entities": ["Invoice"],
                     "targets": {"Invoice": "id"}, "reaches": ["Invoice", "Partner"],
                     "dry_run": False, "description": "…",
                     "input": {"properties": {"criteria": {"$ref": "#/$defs/Criteria"}, …}},
                     "output": {"properties": {"invoice": {"$ref": "#/models/Invoice"}, …}}},
                 "billing.CommandVoidInvoice": {
                     "role": "command", "query": False, "targets": {"Invoice": "invoice_id"},
                     …}},
             "$defs": {"Criteria": {…}, …}}
    export_metamodel(billing, "web/metamodel.json")     the same, written byte for byte

Context: the discovery document a wire serves — OpenAPI, OpenRPC, MCP — publishes what that wire
exposes; this is the bounded context's whole metamodel, for a build. Both are renderings of the
same contracts (`contract_of`), so a model reads the same in each. Deterministic: the same code
writes the same bytes, so the version is the fingerprint of the content and two replicas, or two
builds, agree without sharing anything.

An operation is keyed by its Command's identity (`registered_name`, the key every wire's binding
is listed by) and says what it is for its entity (`role`), whether it only reads (`query`), which
of its fields names each entity's record (`targets`) and what it takes and answers (`input`,
`output`, JSON Schema 2020-12) — protocol-agnostic: how it is reached is each wire's binding. A
use case that reacts to a domain event is no operation a client calls, and is left out.

The domain events are left out too, unless asked for (`events=`): an event is a fact a context
records and others react to, not something a client calls, and most contexts never publish their
facts to a client. Asked for, they are under `events`, by wire name — what another backend
subscribing to them, or a client that wants to know them, reads:

    metamodel_of(billing, events=True)["events"]
        ->  {"billing.invoice.v1.paid": {"title": "InvoicePaid", "description": "…",
                                         "recorded": True, "handled": False,
                                         "schema": {"properties": {"name": {"const":
                                                    "billing.invoice.v1.paid", …}, …}}}}

`recorded`: one of the context's own events — the event class its history reads answer
(`DomainEvents`), or a subclass of it. `handled`: a Feature or ApplicationService of the bus is
registered for it. Without `events=`, the document is byte for byte what it was, and so is its
version.

What the schemas share is written once: an entity is a pointer into `models`, anything else
nested — criteria's schema above all — a pointer into the metamodel's own `$defs`, named by its
class: its name, or, when another class has the same name, its module — so a name never comes
to mean another class because an operation was added.
"""

import dataclasses
import hashlib
import json
import typing
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from sincpro_framework import exceptions
from sincpro_framework.common.naming import registered_name, snake
from sincpro_framework.ddd.entity.entity_id import key_of
from sincpro_framework.ddd.events.domain_event import DomainEvent
from sincpro_framework.ddd.metamodel import contract_of
from sincpro_framework.ddd.metamodel.services.contract import entities_in
from sincpro_framework.ddd.reads.reads import events_of, named_in_type, parquet_written
from sincpro_framework.introspection.entity_tags import Role, only_reads
from sincpro_framework.introspection.operations import OperationDescription, operations_of
from sincpro_framework.introspection.schemas import adapter
from sincpro_framework.sincpro_abstractions import DryRunnable
from sincpro_framework.use_bus import UseFramework

MODELS = "#/models/"
DEFINITIONS = "#/$defs/"


def _pointed(node: Any, pointers: Mapping[str, str]) -> Any:
    """`node`, rebuilt, with every `#/$defs/X` reference pointed where `pointers` says X lives.

    _pointed({"$ref": "#/$defs/Invoice"}, {"Invoice": "#/models/Invoice"})
        ->  {"$ref": "#/models/Invoice"}
    """
    if isinstance(node, dict):
        return {
            key: (
                pointers.get(value[len(DEFINITIONS) :], value)
                if key == "$ref" and isinstance(value, str) and value.startswith(DEFINITIONS)
                else _pointed(value, pointers)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_pointed(one, pointers) for one in node]
    return node


def _referenced(node: Any) -> set[str]:
    """The name of every definition `node` points at (`#/$defs/<name>`), at any depth.

    _referenced({"items": {"$ref": "#/$defs/Line"}, "x": {"$ref": "#/models/Invoice"}})
        ->  {"Line"}
    """
    found: set[str] = set()
    if isinstance(node, dict):
        pointer = node.get("$ref")
        if isinstance(pointer, str) and pointer.startswith(DEFINITIONS):
            found.add(pointer[len(DEFINITIONS) :])
        for value in node.values():
            found |= _referenced(value)
    elif isinstance(node, list):
        for one in node:
            found |= _referenced(one)
    return found


def _shapes(
    annotations: Mapping[tuple[str, str], Any],
    entities: Collection[type],
    inline: Collection[tuple[str, str]] = (),
) -> tuple[dict[tuple[str, str], Any], dict[str, Any]]:
    """What each operation takes and answers, described in one pass, so each definition they
    share is written once and named by its class — and the definitions they still point at.

        _shapes({("billing.QueryGetInvoice", "input"): QueryGetInvoice,
                 ("billing.QueryGetInvoice", "output"): ResponseInvoice}, [Invoice, Partner])
            ->  ({(…, "input"): {"properties": {"criteria": {"$ref": "#/$defs/Criteria"}, …}},
                  (…, "output"): {"properties": {"invoice": {"$ref": "#/models/Invoice"}, …}}},
                 {"Criteria": {…}, …})

    1. Nothing declared (`None`): `None`; an answer of nothing (`NoneType`): `null`; a type
       pydantic cannot describe on its own: a bare object with its title, as every wire
       publishes it.
    2. Every other one, and every entity they reach, in one pass (`TypeAdapter.json_schemas`):
       pydantic names each definition by its class — its name, or its module when two classes
       share the name —, so no name depends on which other operations exist.
    3. An entity's definition pointed at its model (`#/models/<Entity>`); every other one kept.
    4. A schema that is only a pointer to its own class's definition is that definition, written
       in place; an answer that is an entity stays a pointer to its model — but a key `inline`
       names (a domain event, which is an entity too) is always its own definition, in place.
    Final: the schemas by key, and the definitions some schema reaches.
    """
    schemas: dict[tuple[str, str], Any] = {}
    described: list[tuple[Any, Any, TypeAdapter[Any]]] = []
    for key, annotation in annotations.items():
        if annotation is None:
            schemas[key] = None
            continue
        if annotation is type(None):
            schemas[key] = {"type": "null"}
            continue
        try:
            read = adapter(annotation)
            read.json_schema()
        except Exception:
            title = getattr(annotation, "__name__", None)
            schemas[key] = {"type": "object", "title": title} if title else {"type": "object"}
            continue
        described.append((key, "validation", read))
    for entity in entities:
        try:
            read = adapter(entity)
            read.json_schema()
        except Exception:
            continue
        described.append((entity, "validation", read))
    keyed, top = TypeAdapter.json_schemas(described)
    definitions: dict[str, Any] = top.get("$defs", {})
    pointers = {name: f"{DEFINITIONS}{name}" for name in definitions}
    for (key, _), schema in keyed.items():
        if isinstance(key, type) and str(schema.get("$ref", "")).startswith(DEFINITIONS):
            pointers[schema["$ref"][len(DEFINITIONS) :]] = f"{MODELS}{key.__name__}"
    pointed = {name: _pointed(one, pointers) for name, one in definitions.items()}
    for (key, _), schema in keyed.items():
        if isinstance(key, type):
            continue
        raw = str(schema.get("$ref", "")) if set(schema) == {"$ref"} else ""
        if key in inline and raw.startswith(DEFINITIONS):
            schemas[key] = _pointed(definitions[raw[len(DEFINITIONS) :]], pointers)
            continue
        body = _pointed(schema, pointers)
        own = str(body.get("$ref", "")) if set(body) == {"$ref"} else ""
        if own.startswith(DEFINITIONS):
            body = _pointed(definitions[own[len(DEFINITIONS) :]], pointers)
        schemas[key] = body
    reached: set[str] = set()
    waiting = _referenced(schemas)
    while waiting:
        name = waiting.pop()
        reached.add(name)
        waiting |= _referenced(pointed[name]) - reached
    return schemas, {name: pointed[name] for name in sorted(reached)}


def _targets(operation: OperationDescription) -> dict[str, str]:
    """Which field of the operation's Command names the record — or the records — of each entity
    it belongs to: what a client fills with the keys of what it runs the operation on.

        _targets(<CommandVoidInvoice(invoice_id), (Invoice,)>)     ->  {"Invoice": "invoice_id"}
        _targets(<QueryGetInvoice(id, criteria), (Invoice,)>)      ->  {"Invoice": "id"}
        _targets(<QueryGetManyInvoices(ids), (Invoice,)>)          ->  {"Invoice": "ids"}
        _targets(<CommandOpenIssue(project_id), (Issue, Project)>) ->  {"Project": "project_id"}
        _targets(<CommandMoveIssue(issue: EntityID[Issue], destination: EntityID[Project])>)
            ->  {"Issue": "issue", "Project": "destination"}
        _targets(<CommandReconcile(lines: list[EntityID[MoveLine]], payment_ids: list[str]),
                  (MoveLine, Payment)>)
            ->  {"MoveLine": "lines", "Payment": "payment_ids"}    # one use case, N entities
        _targets(<CommandTransfer(source, target: EntityID[Account])>)  ->  {}   # which one?
        _targets(<QueryListInvoices(criteria), (Invoice,)>)        ->  {}

    Per entity, the first rule that names a field; a rule that finds two names none, and
    nothing is guessed. A key may be a list — the records a reconciliation matches —; its
    schema says so.
    1. A field typed as its key (`EntityID[Invoice]`, `list[EntityID[Invoice]]`).
    2. Its own key's name, `<entity>_id` or `<entity>_ids`, unless typed as another's key.
    3. When the operation belongs to that one entity only: its one bare key, else `id` or `ids`.
    Final: the entities named so; one with no such field is no record the operation runs on —
    one it creates, a page of it, the context's own.
    """
    fields = getattr(operation.input, "model_fields", {})
    keys = {
        name: key
        for name, field in fields.items()
        if (key := key_of(field.annotation)) is not None
    }
    untyped = [name for name in fields if name not in keys or keys[name].entity is None]
    bare = [name for name, key in keys.items() if key.entity is None]
    targets: dict[str, str] = {}
    for entity in operation.entities:
        typed = [name for name, key in keys.items() if key.entity is entity]
        stem = snake(entity.__name__)
        own = [one for one in (f"{stem}_id", f"{stem}_ids") if one in untyped]
        if typed:
            named = typed
        elif own:
            named = own
        elif len(operation.entities) > 1:
            named = []
        elif bare:
            named = bare
        else:
            named = [one for one in ("id", "ids") if one in fields and one not in keys]
        if len(named) == 1:
            targets[entity.__name__] = named[0]
    return targets


def _operation(
    operation: OperationDescription,
    reached: Collection[type],
    taken: dict[str, Any] | None,
    answered: dict[str, Any] | None,
) -> dict[str, Any]:
    """One operation as a client reads it, `taken` and `answered` its shapes (`_shapes`).

        _operation(<CommandEditTicket>, [Ticket, Partner], {…}, {…})
            ->  {"role": "edit", "query": False, "entities": ["Ticket"],
                 "targets": {"Ticket": "id"}, "reaches": ["Partner", "Ticket"],
                 "dry_run": False, "description": "…",
                 "input": {"properties": {"values": {"$ref": "#/models/Ticket", …}, …}},
                 "output": {…}}

    1. What it is for its entity (`role`), and whether it only reads (`query`): a framework read
       of an entity, or a Command that is a `Query`.
    2. The entities it belongs to, the field naming each one's record (`_targets`), and every
       entity it reaches.
    3. An edit's and a preview's `values` is the record the form holds: its entity's model.
    4. A search says how its page may travel: `formats`, and past how many records asked for it
       answers Parquet to a caller that reads it (`parquet_above`).
    Final: with whether it runs dry, its description, and what it takes and answers.
    """
    named = named_in_type(operation.input)
    if (
        operation.role in (Role.EDIT, Role.PREVIEW)
        and named is not None
        and taken is not None
        and "values" in taken.get("properties", {})
    ):
        values = taken["properties"]["values"]
        taken["properties"]["values"] = {
            "$ref": f"{MODELS}{named[0].__name__}",
            **({"description": values["description"]} if "description" in values else {}),
        }
    formats: dict[str, Any] = {}
    if operation.role is Role.SEARCH:
        above = getattr(operation.input, "parquet_above", None)
        parquet = above is not None and parquet_written()
        formats = {
            "formats": ["json", "parquet"] if parquet else ["json"],
            "parquet_above": above if parquet else None,
        }
    return {
        **formats,
        "role": str(operation.role),
        "query": only_reads(operation.input),
        "entities": [one.__name__ for one in operation.entities],
        "targets": _targets(operation),
        "reaches": sorted({one.__name__ for one in reached}),
        "dry_run": issubclass(operation.input, DryRunnable),
        "description": operation.description,
        "input": taken,
        "output": answered,
    }


def _modelled(contract: dict[str, Any], name: str, models: dict[str, Any]) -> None:
    """`contract` held in `models` under `name`, what it holds beside it (`$defs` — the entities
    its relations point at, the shapes its records hold inside) held there too, once each.

        _modelled(contract_of(Sale), "Sale", models)
            ->  models["Sale"], models["Customer"], models["SaleLine"]
        # another contract already holds a `SaleLine` of another shape:
            ->  models["SaleLine.Sale"], `Sale` pointing at it

    Context: one name may be two classes across contracts — a shape a record holds is named by
    its class alone —; the second is held qualified by the entity it came with, its pointers
    moved with it, so no model points at a shape not its own.
    """
    beside = contract.pop("$defs", {})
    renamed = {
        one: one if models.get(one, schema) == schema else f"{one}.{name}"
        for one, schema in beside.items()
    }
    pointers = {one: f"{DEFINITIONS}{new}" for one, new in renamed.items() if new != one}
    for one, schema in beside.items():
        models.setdefault(renamed[one], _pointed(schema, pointers))
    models[name] = _pointed(contract, pointers)


def _is_event(annotation: Any) -> bool:
    """Whether `annotation` is a domain event class."""
    return isinstance(annotation, type) and issubclass(annotation, DomainEvent)


def _subclasses(base: type) -> list[type]:
    """Every subclass of `base`, at any depth, each once, ordered by name.

    _subclasses(TicketEvent)    ->  [TicketClosed, TicketReopened, TicketReopenedByAgent]
    """
    found: dict[type, None] = {}
    waiting = list(base.__subclasses__())
    while waiting:
        one = waiting.pop()
        if one not in found:
            found[one] = None
            waiting += one.__subclasses__()
    return sorted(found, key=lambda one: (one.__name__, one.__module__))


def _event_classes(
    described: Mapping[str, OperationDescription],
    asked: bool | Iterable[type[DomainEvent]],
) -> tuple[dict[str, type[DomainEvent]], tuple[type, ...], set[type]]:
    """The events a metamodel describes, by wire name — with the context's own event classes (the
    ones its history reads answer) and the ones the bus is registered for.

        _event_classes(operations_of(desk), True)
            ->  ({"TicketEvent": TicketEvent, "desk.ticket.v1.closed": TicketClosed},
                 (TicketEvent,), {TicketEvent})
        _event_classes(operations_of(desk), [TicketClosed])
            ->  ({"desk.ticket.v1.closed": TicketClosed}, (TicketEvent,), {TicketEvent})

    1. The context's own event classes: the one each `DomainEvents` read answers (`events_of`).
    2. The events the bus handles: each DomainEvent a Feature or ApplicationService is
       registered for.
    3. Asked as `True`: those handled, and every subclass of the context's own — what its
       aggregates record —, never the context's base itself unless the bus handles it (it is a
       model already). Asked as classes: exactly those, each a DomainEvent or refused.
    Final: by wire name, sorted; two classes with one wire name are refused, naming both — the
    name is what crosses contexts, so it means one event.
    """
    bases: dict[type, None] = {}
    handled: set[type] = set()
    for operation in described.values():
        if _is_event(operation.input):
            handled.add(operation.input)
        elif operation.role is Role.EVENTS and isinstance(operation.answer, type):
            bases.setdefault(events_of(operation.answer), None)
    if asked is True:
        chosen = [
            *sorted(handled, key=lambda one: (one.__name__, one.__module__)),
            *(one for base in bases for one in _subclasses(base)),
        ]
    else:
        chosen = list(asked)  # type: ignore[arg-type]
        for one in chosen:
            if not _is_event(one):
                raise exceptions.ProgrammingError(
                    f"{one!r} is not a DomainEvent class: only domain events are exported as "
                    "events"
                )
    events: dict[str, type[DomainEvent]] = {}
    for one in chosen:
        held = events.setdefault(one.name, one)
        if held is not one:
            raise exceptions.ProgrammingError(
                f"{held.__module__}.{held.__name__} and {one.__module__}.{one.__name__} are both "
                f"named {one.name!r} on the wire: an event's name means one event"
            )
    return dict(sorted(events.items())), tuple(bases), handled


def _reached_by_event(event: type) -> list[type]:
    """The entities an event's fields reach — never the event itself, which is an entity too.

    _reached_by_event(TicketClosed)    ->  [Partner]       # by: Partner | None
    """
    seen: set[Any] = {event}
    try:
        hints = typing.get_type_hints(event)
    except Exception:
        hints = {}
    reached: list[type] = []
    for field in dataclasses.fields(event):
        if field.name in hints:
            reached += entities_in(hints[field.name], seen)
    return reached


def _first_paragraph(text: str | None) -> str | None:
    """What a description says first: its first paragraph, or `None` when there is none."""
    if not text:
        return None
    first = text.strip().split("\n\n", 1)[0].strip()
    return first or None


def _event(
    event: type[DomainEvent],
    schema: dict[str, Any] | None,
    bases: tuple[type, ...],
    handled: set[type],
) -> dict[str, Any]:
    """One event as a client reads it, `schema` its shape (`_shapes`).

        _event(TicketClosed, {…}, (TicketEvent,), set())
            ->  {"title": "TicketClosed", "description": "A ticket was closed.",
                 "recorded": True, "handled": False,
                 "schema": {"properties": {"name": {"const": "desk.ticket.v1.closed",
                            "type": "string"}, "reason": {…}, …},
                            "required": ["name", "reason"], …}}

    1. Its shape, with its wire name as a constant `name` — the field its JSON (`as_json`)
       carries, what a reader tells one event from another by —, required.
    Final: its class's name, the first paragraph of its description, whether it is one of the
    context's own (`recorded`) and whether the bus handles it (`handled`).
    """
    body: dict[str, Any] = dict(schema or {"type": "object", "title": event.__name__})
    body["properties"] = {
        "name": {"const": event.name, "type": "string"},
        **body.get("properties", {}),
    }
    body["required"] = ["name", *(one for one in body.get("required", []) if one != "name")]
    return {
        "title": event.__name__,
        "description": _first_paragraph(body.get("description")),
        "recorded": any(issubclass(event, base) for base in bases),
        "handled": event in handled,
        "schema": body,
    }


def metamodel_of(
    bus: UseFramework,
    operations: Collection[str] | None = None,
    events: bool | Iterable[type[DomainEvent]] = False,
) -> dict[str, Any]:
    """Every model `bus` reaches, the operations a client calls, and the version of the whole —
    of the `operations` named (their Commands' class names), when a wire publishes only what
    it exposes.

        metamodel_of(billing)                                  the whole bounded context
        metamodel_of(billing, {"QueryGetInvoice"})             what that one operation reaches
        metamodel_of(billing, events=True)                     and its domain events
        metamodel_of(billing, events=[InvoicePaid])            and exactly those events

    1. Each operation of the bus (`operations_of`) — of those named, when named — a client
       calls: not a use case that reacts to a domain event. Keyed by its Command's identity
       (`registered_name`, `billing.QueryGetInvoice`).
    2. The entities each one belongs to (`entity_tags`, the entity a framework DTO names), and
       every entity its Command and its answer reach — the answer `execute` declares, else the
       one its DTO names (`answer`) —, each as its contract (`contract_of`), the entities its
       relations point at and the shapes its records hold beside it, by name, once each
       (`_modelled`); every pointer between models pointed at `models`.
    3. Asked for events: those of the whole bus — not only of the operations named
       (`_event_classes`) —, and the entities their fields reach as models too.
    4. What every operation takes and answers, and every event is, in one pass (`_shapes`).
    5. Each operation as a client reads it (`_operation`); each event (`_event`).
    Final: the bounded context's name, the models, the operations and the shared definitions —
    and the events, only when asked —, with the fingerprint of all of them as the version.
    """
    described = operations_of(bus)
    chosen = {
        registered_name(operation.input, bus.name): operation
        for name, operation in described.items()
        if (operations is None or name in operations) and not _is_event(operation.input)
    }
    models: dict[str, Any] = {}
    reaches: dict[str, list[type]] = {}
    for identity, operation in chosen.items():
        seen: set[Any] = set()
        reaches[identity] = [
            *operation.entities,
            *entities_in(operation.input, seen),
            *entities_in(operation.answer, seen),
        ]
        for entity in reaches[identity]:
            if entity.__name__ not in models:
                _modelled(contract_of(entity), entity.__name__, models)
    told, bases, handled = (
        _event_classes(described, events) if events is not False else ({}, (), set())
    )
    event_reaches: list[type] = []
    for event in told.values():
        for entity in _reached_by_event(event):
            event_reaches.append(entity)
            if entity.__name__ not in models:
                _modelled(contract_of(entity), entity.__name__, models)
    models = {
        name: _pointed(model, {one: f"{MODELS}{one}" for one in models})
        for name, model in models.items()
    }
    shapes, definitions = _shapes(
        {
            **{
                (identity, side): annotation
                for identity, operation in chosen.items()
                for side, annotation in (
                    ("input", operation.input),
                    ("output", operation.answer),
                )
            },
            **{(name, "event"): event for name, event in told.items()},
        },
        list(
            dict.fromkeys(
                [
                    *(entity for reached in reaches.values() for entity in reached),
                    *event_reaches,
                ]
            )
        ),
        inline={(name, "event") for name in told},
    )
    content = {
        "bounded_context": bus.name,
        "models": models,
        "operations": {
            identity: _operation(
                operation,
                reaches[identity],
                shapes[(identity, "input")],
                shapes[(identity, "output")],
            )
            for identity, operation in chosen.items()
        },
        "$defs": definitions,
    }
    if events is not False:
        content["events"] = {
            name: _event(event, shapes[(name, "event")], bases, handled)
            for name, event in told.items()
        }
    written = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return {"version": hashlib.sha256(written.encode()).hexdigest(), **content}


def export_metamodel(
    bus: UseFramework, path: str | Path, events: bool | Iterable[type[DomainEvent]] = False
) -> str:
    """Write the metamodel of `bus` to `path` as JSON — keys sorted, indented, ending in a
    newline, so the same code writes the same bytes and a diff shows only what changed — and
    answer its version.

        export_metamodel(billing, "web/src/metamodel.json")                 ->  "9b1c…"
        export_metamodel(billing, "web/src/metamodel.json", events=True)    ->  with its events
    """
    metamodel = metamodel_of(bus, events=events)
    Path(path).write_text(
        json.dumps(metamodel, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        + "\n",
        encoding="utf-8",
    )
    return metamodel["version"]
