"""The metamodel of a bus: every model its operations reach, each as its published contract, with
the operations that belong to each, under one version — what a build writes to a file for a client
that works offline, generates its types from, or ships as fixtures.

    metamodel_of(billing)
        ->  {"version": "9b1c…",
             "models": {"Invoice": {"type": "object", "x-sincpro": {…}, …}, "Partner": {…}},
             "operations": {"QueryGetInvoice": {"entities": ["Invoice"], "reaches": ["Invoice"],
                                                "dry_run": False, "description": "…"}, …}}
    export_metamodel(billing, "web/metamodel.json")     the same, written byte for byte

Context: the discovery document a wire serves — OpenAPI, OpenRPC, MCP — publishes what that wire
exposes; this is the bounded context's whole metamodel, for a build. Both are renderings of the
same contracts (`contract_of`), so a model reads the same in each. Deterministic: the same code
writes the same bytes, so the version is the fingerprint of the content and two replicas, or two
builds, agree without sharing anything.
"""

import hashlib
import json
from collections.abc import Collection
from pathlib import Path
from typing import Any

from sincpro_framework.ddd.metamodel import contract_of
from sincpro_framework.ddd.metamodel.services.contract import entities_in
from sincpro_framework.introspection.operations import operations_of
from sincpro_framework.sincpro_abstractions import DryRunnable
from sincpro_framework.use_bus import UseFramework


def metamodel_of(
    bus: UseFramework, operations: Collection[str] | None = None
) -> dict[str, Any]:
    """Every model `bus` reaches, its operations, and the version of the whole — of the
    `operations` named, when a wire publishes only what it exposes.

        metamodel_of(billing)                                  the whole bounded context
        metamodel_of(billing, {"QueryGetInvoice"})             what that one operation reaches

    1. Each operation of the bus (`operations_of`) — of those named, when named —: the entities
       it belongs to (`entity_tags`, the entity a framework DTO names), and every entity its
       Command and its answer reach.
    2. Each entity reached, as its contract (`contract_of`); the entities its relations point at
       beside it, by name, once each.
    3. Each operation as the entities it belongs to, the ones it reaches, whether its Command may
       be run dry (`DryRunnable`) and its description.
    Final: the models and the operations, with the fingerprint of both as the version.
    """
    models: dict[str, Any] = {}
    described: dict[str, Any] = {}
    for name, operation in operations_of(bus).items():
        if operations is not None and name not in operations:
            continue
        seen: set[Any] = set()
        reached = [
            *operation.entities,
            *entities_in(operation.input, seen),
            *entities_in(operation.response, seen),
        ]
        for entity in reached:
            if entity.__name__ in models:
                continue
            contract = contract_of(entity)
            for related, schema in contract.pop("$defs", {}).items():
                models.setdefault(related, schema)
            models[entity.__name__] = contract
        described[name] = {
            "entities": [one.__name__ for one in operation.entities],
            "reaches": sorted({one.__name__ for one in reached}),
            "dry_run": issubclass(operation.input, DryRunnable),
            "description": operation.description,
        }
    content = {"models": models, "operations": described}
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
