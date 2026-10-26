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
wrote still verifies, so a manifest hashed by an older release goes on working until `hash`
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

from sincpro_framework.data_layer.migrations.domain import exceptions
from sincpro_framework.data_layer.migrations.domain.step import Step

MANIFEST = "meta_migration.json"
FORMAT = 1
ALGORITHM = "v2"
SUM_ALGORITHM = "v1"


@dataclass
class StoreSteps:
    """One store of the manifest: the engine its step bodies were written for, and its steps
    in chain order.
    """

    engine: str
    steps: list[Step] = field(default_factory=list)


def _text(path: Path) -> bytes:
    """A body's bytes with CRLF line endings turned into LF, so a checkout on Windows hashes
    the same as one on Linux.
    """
    return path.read_bytes().replace(b"\r\n", b"\n")


def _used_names(tree: ast.Module) -> set[str]:
    """Every name the module reads or writes anywhere — what decides whether an import is
    used."""
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
            # The name the import binds: its `as` name, or the first part of a dotted module
            # (`import sqlalchemy.orm` binds `sqlalchemy`).
            if (alias.asname or alias.name.split(".")[0]) in used:
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
    """The sha256 of a body's text, line endings normalised — `v1`, for any body that is not
    Python and for the manifest's `sum`.
    """
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
        raise exceptions.MigrationRefused(
            f"{path} is not Python that parses: {error}"
        ) from error
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


def _raw(step: Step) -> dict[str, Any]:
    """A step as the manifest writes it: `requires` and `irreversible` only when they say
    something, so a manifest of ordinary steps stays short and diffs stay small.
    """
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


@dataclass
class Manifest:
    """A context's `meta_migration.json` in memory: its stores by name, and the `sum` that
    covers every step of every store.

        manifest = Manifest.read(Path("migrations/chat"), "chat")
        manifest.stores["main"].steps.append(step)
        manifest.write(Path("migrations/chat"))       its `sum` recomputed first
    """

    context: str
    stores: dict[str, StoreSteps] = field(default_factory=dict)
    sum: str = ""

    @classmethod
    def checksum(cls, body: Path) -> str:
        """A body's checksum with the current algorithm, prefixed with its name — so `matches`
        still verifies it after the current algorithm changes.

            Manifest.checksum(Path("chat/main/0192…_add_read_at.py"))  ->  "v2:5e1f…"
        """
        return f"{ALGORITHM}:{DIGESTS[ALGORITHM](body)}"

    @classmethod
    def matches(cls, body: Path, recorded: str) -> bool:
        """Whether the body still is what `recorded` hashed, by the algorithm that hashed it —
        never, for an algorithm this framework does not know."""
        algorithm, _, digest = recorded.partition(":")
        return algorithm in DIGESTS and DIGESTS[algorithm](body) == digest

    @classmethod
    def read(cls, folder: Path, context: str) -> "Manifest":
        """The context's manifest; an empty one when the context has no step yet.

            Manifest.read(Path("migrations/chat"), "chat")
                ->  Manifest("chat", {"main": StoreSteps("alembic", [Step(...), ...])}, "v1:…")
            Manifest.read(Path("migrations/new"), "billing")
                ->  Manifest(context="billing", stores={}, sum="")

        A file of another format, or written for another context, is refused as
        `MigrationRefused` — each context has a folder of its own.
        """
        path = folder / MANIFEST
        if not path.exists():
            return cls(context)
        raw = json.loads(path.read_text())
        if raw["format"] != FORMAT or raw["context"] != context:
            raise exceptions.MigrationRefused(
                f"{path} is format {raw['format']} of context {raw['context']}; this code "
                f"reads format {FORMAT} of context {context} — a folder per context, and the "
                "framework that wrote it"
            )
        # `requires` and `irreversible` absent from the JSON mean none and false.
        stores = {
            store: StoreSteps(
                entry["engine"],
                [
                    Step(
                        id=one["id"],
                        context=context,
                        store=store,
                        parent=one["parent"],
                        message=one["message"],
                        file=one["file"],
                        checksum=one["checksum"],
                        requires=tuple(one.get("requires", ())),
                        irreversible=one.get("irreversible", False),
                    )
                    for one in entry["steps"]
                ],
            )
            for store, entry in raw["stores"].items()
        }
        return cls(context, stores, raw["sum"])

    def computed_sum(self) -> str:
        """The checksum of the whole manifest as its steps are now: one line per step —
        `store/id:checksum`, stores in name order, steps in chain order — hashed as `v1`.

        Context: two branches that each add a step change this one value, so they meet as a git
        conflict on the manifest rather than as two heads in production.
        """
        lines = [
            f"{store}/{step.id}:{step.checksum}"
            for store in sorted(self.stores)
            for step in self.stores[store].steps
        ]
        return f"{SUM_ALGORITHM}:{hashlib.sha256('\n'.join(lines).encode()).hexdigest()}"

    def write(self, folder: Path) -> None:
        """The manifest written to `<folder>/meta_migration.json`, its `sum` recomputed first —
        stores in name order, two-space indented, so the same steps always write the same file.
        """
        self.sum = self.computed_sum()
        raw = {
            "format": FORMAT,
            "context": self.context,
            "stores": {
                store: {"engine": entry.engine, "steps": [_raw(step) for step in entry.steps]}
                for store, entry in sorted(self.stores.items())
            },
            "sum": self.sum,
        }
        folder.mkdir(parents=True, exist_ok=True)
        (folder / MANIFEST).write_text(json.dumps(raw, indent=2) + "\n")
