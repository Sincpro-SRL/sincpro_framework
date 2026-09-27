"""`meta_migration.json`: a context's steps, per store, as the code knows them — written by
`revision` and `hash`, never by hand.

    {"format": 1, "context": "chat",
     "stores": {"main": {"engine": "alembic", "steps": [{"id": …, "parent": …, "message": …,
                                                          "file": …, "checksum": "v1:…"}]}},
     "sum": "v1:…"}

Context: a checksum per step catches a body edited after it was hashed; `sum` covers every step
of the context, so two branches that each add a step change the same line and meet as a git
conflict instead of as two heads in production. Line endings are normalised before hashing, and
the algorithm is named in the value — a change of algorithm is a new prefix, never a silent
mismatch.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sincpro_framework.migrations.domain import MigrationRefused, Step

MANIFEST = "meta_migration.json"
FORMAT = 1
ALGORITHM = "v1"


@dataclass
class StoreSteps:
    engine: str
    steps: list[Step] = field(default_factory=list)


@dataclass
class Manifest:
    context: str
    stores: dict[str, StoreSteps] = field(default_factory=dict)
    sum: str = ""


def checksum(path: Path) -> str:
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return f"{ALGORITHM}:{hashlib.sha256(content).hexdigest()}"


def sum_of(manifest: Manifest) -> str:
    lines = [
        f"{store}/{step.id}:{step.checksum}"
        for store in sorted(manifest.stores)
        for step in manifest.stores[store].steps
    ]
    return f"{ALGORITHM}:{hashlib.sha256('\n'.join(lines).encode()).hexdigest()}"


def _step(context: str, store: str, raw: dict[str, Any]) -> Step:
    return Step(
        id=raw["id"],
        context=context,
        store=store,
        parent=raw["parent"],
        message=raw["message"],
        file=raw["file"],
        checksum=raw["checksum"],
        requires=tuple(raw.get("requires", ())),
        irreversible=raw.get("irreversible", False),
    )


def _raw(step: Step) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": step.id,
        "parent": step.parent,
        "message": step.message,
        "file": step.file,
        "checksum": step.checksum,
    }
    if step.requires:
        raw["requires"] = list(step.requires)
    if step.irreversible:
        raw["irreversible"] = True
    return raw


def read_manifest(folder: Path, context: str) -> Manifest:
    """The context's manifest; an empty one when the context has no step yet."""
    path = folder / MANIFEST
    if not path.exists():
        return Manifest(context)
    raw = json.loads(path.read_text())
    if raw["format"] != FORMAT or raw["context"] != context:
        raise MigrationRefused(
            f"{path} is format {raw['format']} of context {raw['context']}; this code reads "
            f"format {FORMAT} of context {context} — a folder per context, and the framework "
            "that wrote it"
        )
    stores = {
        store: StoreSteps(
            entry["engine"], [_step(context, store, one) for one in entry["steps"]]
        )
        for store, entry in raw["stores"].items()
    }
    return Manifest(context, stores, raw["sum"])


def write_manifest(folder: Path, manifest: Manifest) -> None:
    manifest.sum = sum_of(manifest)
    raw = {
        "format": FORMAT,
        "context": manifest.context,
        "stores": {
            store: {"engine": entry.engine, "steps": [_raw(step) for step in entry.steps]}
            for store, entry in sorted(manifest.stores.items())
        },
        "sum": manifest.sum,
    }
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MANIFEST).write_text(json.dumps(raw, indent=2) + "\n")
