"""`meta_migration.json`: a context's steps, per store, as the code knows them — written by
`revision` and `hash`, never by hand.

    {"format": 1, "context": "chat",
     "stores": {"main": {"engine": "alembic", "steps": [{"id": …, "parent": …, "message": …,
                                                          "file": …, "checksum": "v2:…"}]}},
     "sum": "v1:…"}

Context: a checksum per step catches a body edited after it was hashed; `sum` covers every step
of the context, so two branches that each add a step change the same line and meet as a git
conflict instead of as two heads in production. The algorithm is named in the value — a change
of algorithm is a new prefix, never a silent mismatch — and every algorithm this framework ever
wrote still verifies, so a manifest hashed by an older release keeps working until `hash`
rewrites it:

- `v1`: the body's bytes, line endings normalised.
- `v2`: what a Python body does, not how it is laid out — `make format` rewrites generated
  bodies, and a checksum that changed with them would refuse every formatted step. Any other
  body is hashed as `v1` hashes it.
"""

import ast
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sincpro_framework.data_layer.migrations.domain.step import MigrationRefused, Step

MANIFEST = "meta_migration.json"
FORMAT = 1
ALGORITHM = "v2"
SUM_ALGORITHM = "v1"


@dataclass
class StoreSteps:
    engine: str
    steps: list[Step] = field(default_factory=list)


@dataclass
class Manifest:
    context: str
    stores: dict[str, StoreSteps] = field(default_factory=dict)
    sum: str = ""


def _text(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def _bound_name(alias: ast.alias) -> str:
    return alias.asname or alias.name.split(".")[0]


def _used_names(tree: ast.Module) -> set[str]:
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _imports(tree: ast.Module) -> list[str]:
    """Every module-level import as one sorted line per name it binds and the code uses.

    Context: isort reorders and regroups imports and autoflake removes the unused ones; which
    names are bound to what is the only part that changes what the step does."""
    used = _used_names(tree)
    lines: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            module = ""
        elif isinstance(statement, ast.ImportFrom):
            module = f"{'.' * statement.level}{statement.module or ''}"
        else:
            continue
        for alias in statement.names:
            if _bound_name(alias) in used:
                lines.add(f"{module}:{alias.name}:{alias.asname}")
    return sorted(lines)


def _collapse_docstrings(tree: ast.Module) -> None:
    """A docstring stays content, its indentation and wrapping do not — black re-indents them."""
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            first.value.value = " ".join(first.value.value.split())


def _v1_digest(path: Path) -> str:
    return hashlib.sha256(_text(path)).hexdigest()


def _v2_digest(path: Path) -> str:
    """Context: the syntax tree drops comments, quotes, parentheses, trailing commas and line
    wrapping; imports and docstrings are normalised on top of it.

    1. A body that is not Python is hashed as its text.
    2. Parse it; a body that does not parse is refused, naming it.
    3. Final: hash its imports, then every other statement of the tree, docstrings collapsed.
    """
    if path.suffix != ".py":
        return _v1_digest(path)
    try:
        tree = ast.parse(_text(path))
    except SyntaxError as error:
        raise MigrationRefused(f"{path} is not Python that parses: {error}") from error
    _collapse_docstrings(tree)
    statements = [
        ast.dump(statement)
        for statement in tree.body
        if not isinstance(statement, (ast.Import, ast.ImportFrom))
    ]
    normalised = "\n".join([*_imports(tree), *statements])
    return hashlib.sha256(normalised.encode()).hexdigest()


DIGESTS: dict[str, Callable[[Path], str]] = {"v1": _v1_digest, "v2": _v2_digest}
"""Every checksum algorithm, by the prefix it writes."""


def checksum(path: Path) -> str:
    return f"{ALGORITHM}:{DIGESTS[ALGORITHM](path)}"


def matches(path: Path, recorded: str) -> bool:
    """Whether the body still is what `recorded` hashed, by the algorithm that hashed it — never,
    for an algorithm this framework does not know."""
    algorithm, _, digest = recorded.partition(":")
    return algorithm in DIGESTS and DIGESTS[algorithm](path) == digest


def sum_of(manifest: Manifest) -> str:
    lines = [
        f"{store}/{step.id}:{step.checksum}"
        for store in sorted(manifest.stores)
        for step in manifest.stores[store].steps
    ]
    return f"{SUM_ALGORITHM}:{hashlib.sha256('\n'.join(lines).encode()).hexdigest()}"


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
