"""The metamodel of a bus: every model its operations reach, each as its published contract, with
the operations a client calls on each, under one version — what a build writes to a file for a
client that works offline, generates its types and its client from, or ships as fixtures.

    metamodel_of(billing)
        ->  {"context": "billing",
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

What the schemas share is written once: an entity is a pointer into `models`, anything else
nested — criteria's schema above all — a pointer into the metamodel's own `$defs`, named by its
class: its name, or, when another class has the same name, its module — so a name never comes
to mean another class because an operation was added.
"""

import hashlib
import json
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from sincpro_framework.common.naming import registered_name, snake
from sincpro_framework.ddd.entity.entity_id import key_of
from sincpro_framework.ddd.events.domain_event import DomainEvent
from sincpro_framework.ddd.metamodel import contract_of
from sincpro_framework.ddd.metamodel.services.contract import entities_in
from sincpro_framework.ddd.query import Query
from sincpro_framework.ddd.reads.reads import named_in_type
from sincpro_framework.introspection.entity_tags import Role
from sincpro_framework.introspection.operations import OperationDescription, operations_of
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
    annotations: Mapping[tuple[str, str], Any], entities: Collection[type]
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
       in place; an answer that is an entity stays a pointer to its model.
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
            adapter = TypeAdapter(annotation)
            adapter.json_schema()
        except Exception:
            title = getattr(annotation, "__name__", None)
            schemas[key] = {"type": "object", "title": title} if title else {"type": "object"}
            continue
        described.append((key, "validation", adapter))
    for entity in entities:
        try:
            adapter = TypeAdapter(entity)
            adapter.json_schema()
        except Exception:
            continue
        described.append((entity, "validation", adapter))
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
    """Which field of the operation's Command names the record of each entity it belongs to —
    what a client fills with the record's key to run it on that record.

        _targets(<CommandVoidInvoice(invoice_id), (Invoice,)>)     ->  {"Invoice": "invoice_id"}
        _targets(<QueryGetInvoice(id, criteria), (Invoice,)>)      ->  {"Invoice": "id"}
        _targets(<CommandOpenIssue(project_id), (Issue, Project)>) ->  {"Project": "project_id"}
        _targets(<CommandMoveIssue(issue: EntityID[Issue], destination: EntityID[Project])>)
            ->  {"Issue": "issue", "Project": "destination"}
        _targets(<CommandTransfer(source, target: EntityID[Account])>)  ->  {}   # which one?
        _targets(<QueryListInvoices(criteria), (Invoice,)>)        ->  {}

    Per entity, the first rule that names a field; a rule that finds two names none, and
    nothing is guessed:
    1. A field typed as its key (`EntityID[Invoice]`).
    2. Its own key's name, `<entity>_id` (`invoice_id`), unless typed as another's key.
    3. When the operation belongs to that one entity only: its one bare `EntityID`, else `id`.
    Final: the entities named so; one with no such field is no record the operation runs on —
    one it creates, a page of it, the context's own.
    """
    fields = getattr(operation.input, "model_fields", {})
    keys = {
        name: key
        for name, field in fields.items()
        if (key := key_of(field.annotation)) is not None
    }
    bare = [name for name, key in keys.items() if key.entity is None]
    targets: dict[str, str] = {}
    for entity in operation.entities:
        typed = [name for name, key in keys.items() if key.entity is entity]
        own = f"{snake(entity.__name__)}_id"
        if typed:
            named = typed
        elif own in fields and (own not in keys or keys[own].entity is None):
            named = [own]
        elif len(operation.entities) > 1:
            named = []
        elif bare:
            named = bare
        else:
            named = ["id"] if "id" in fields and "id" not in keys else []
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
    return {
        "role": str(operation.role),
        "query": issubclass(operation.input, Query)
        or operation.role not in (Role.EDIT, Role.COMMAND),
        "entities": [one.__name__ for one in operation.entities],
        "targets": _targets(operation),
        "reaches": sorted({one.__name__ for one in reached}),
        "dry_run": issubclass(operation.input, DryRunnable),
        "description": operation.description,
        "input": taken,
        "output": answered,
    }


def metamodel_of(
    bus: UseFramework, operations: Collection[str] | None = None
) -> dict[str, Any]:
    """Every model `bus` reaches, the operations a client calls, and the version of the whole —
    of the `operations` named (their Commands' class names), when a wire publishes only what
    it exposes.

        metamodel_of(billing)                                  the whole bounded context
        metamodel_of(billing, {"QueryGetInvoice"})             what that one operation reaches

    1. Each operation of the bus (`operations_of`) — of those named, when named — a client
       calls: not a use case that reacts to a domain event. Keyed by its Command's identity
       (`registered_name`, `billing.QueryGetInvoice`).
    2. The entities each one belongs to (`entity_tags`, the entity a framework DTO names), and
       every entity its Command and its answer reach — the answer `execute` declares, else the
       one its DTO names (`answer`) —, each as its contract (`contract_of`), the entities its
       relations point at beside it, by name, once each.
    3. What every operation takes and answers, in one pass (`_shapes`).
    4. Each operation as a client reads it (`_operation`).
    Final: the context, the models, the operations and the shared definitions, with the
    fingerprint of all of them as the version.
    """
    chosen = {
        registered_name(operation.input, bus.name): operation
        for name, operation in operations_of(bus).items()
        if (operations is None or name in operations)
        and not (
            isinstance(operation.input, type) and issubclass(operation.input, DomainEvent)
        )
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
            if entity.__name__ in models:
                continue
            contract = contract_of(entity)
            for related, schema in contract.pop("$defs", {}).items():
                models.setdefault(related, schema)
            models[entity.__name__] = contract
    shapes, definitions = _shapes(
        {
            (identity, side): annotation
            for identity, operation in chosen.items()
            for side, annotation in (("input", operation.input), ("output", operation.answer))
        },
        list(dict.fromkeys(entity for reached in reaches.values() for entity in reached)),
    )
    content = {
        "context": bus.name,
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
    written = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return {"version": hashlib.sha256(written.encode()).hexdigest(), **content}


def export_metamodel(bus: UseFramework, path: str | Path) -> str:
    """Write the metamodel of `bus` to `path` as JSON — keys sorted, indented, ending in a
    newline, so the same code writes the same bytes and a diff shows only what changed — and
    answer its version.

        export_metamodel(billing, "web/src/metamodel.json")    ->  "9b1c…"
    """
    metamodel = metamodel_of(bus)
    Path(path).write_text(
        json.dumps(metamodel, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        + "\n",
        encoding="utf-8",
    )
    return metamodel["version"]
