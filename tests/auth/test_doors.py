"""auth is one sub-context per job, each in its own layers: `common` is the shared kernel,
`access_control` the guard on the bus, `server` verifies what arrives, `client` presents what goes
out, `grants` are roles and groups, `accounts` are the service's own. The root of auth is only
its public door; the sub-contexts depend on each other only as stated, and every public name has
one door — what keeps "where is it" answered by the import alone."""

import ast
import importlib
from pathlib import Path

import pytest

AUTH = Path(__file__).parents[2] / "sincpro_framework" / "auth"
DOORS = ("server", "client", "grants", "accounts")
"""The sub-contexts a project imports from, besides the root."""
SUB_CONTEXTS = ("common", "access_control", *DOORS)
MAY_IMPORT = {
    "common": set(),
    "access_control": {"common"},
    "server": {"common"},
    "client": {"common"},
    "grants": {"common"},
    "accounts": {"common", "server"},
}
"""The sub-contexts each one builds on: the shared kernel builds on none, and the issuer of
accounts verifies what it issued with the server's `JwtProvider`."""


def _sub_contexts_imported(path: Path) -> set[str]:
    """The sub-contexts a module imports, by any absolute import of `sincpro_framework.auth.<one>`."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        modules = (
            [node.module]
            if isinstance(node, ast.ImportFrom) and node.module
            else [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
        )
        for module in modules:
            parts = module.split(".")
            if parts[:2] == ["sincpro_framework", "auth"] and len(parts) > 2:
                if parts[2] in SUB_CONTEXTS:
                    found.add(parts[2])
    return found


def test_the_root_is_only_the_public_door() -> None:
    assert sorted(path.name for path in AUTH.glob("*.py")) == ["__init__.py"]
    assert {path.name for path in AUTH.iterdir() if path.is_dir()} - {"__pycache__"} == set(
        SUB_CONTEXTS
    )


@pytest.mark.parametrize("sub_context", SUB_CONTEXTS)
def test_a_sub_context_builds_only_on_the_ones_it_states(sub_context: str) -> None:
    reached = {
        str(path.relative_to(AUTH)): _sub_contexts_imported(path)
        - {sub_context}
        - MAY_IMPORT[sub_context]
        for path in sorted((AUTH / sub_context).rglob("*.py"))
        if "__pycache__" not in path.parts
    }
    assert {module: found for module, found in reached.items() if found} == {}


def test_every_public_name_has_one_door() -> None:
    doors = {
        name: importlib.import_module(name)
        for name in (
            "sincpro_framework.auth",
            *(f"sincpro_framework.auth.{one}" for one in DOORS),
        )
    }
    held: dict[str, list[str]] = {}
    for door, module in doors.items():
        for name in [*module.__all__, *getattr(module, "_LAZY", {})]:
            held.setdefault(name, []).append(door)
    assert {name: where for name, where in held.items() if len(where) > 1} == {}


def test_a_name_is_defined_inside_the_door_that_exports_it() -> None:
    for door in DOORS:
        module = importlib.import_module(f"sincpro_framework.auth.{door}")
        outside = {
            name: getattr(module, name).__module__
            for name in module.__all__
            if not getattr(module, name).__module__.startswith(
                f"sincpro_framework.auth.{door}"
            )
        }
        lazy_outside = {
            name: where
            for name, where in getattr(module, "_LAZY", {}).items()
            if not where.startswith(f"sincpro_framework.auth.{door}")
        }
        assert outside == {} and lazy_outside == {}, door


@pytest.mark.parametrize("door", ("", *DOORS))
def test_a_star_import_needs_no_extra(door: str) -> None:
    module = importlib.import_module("sincpro_framework.auth" + (f".{door}" if door else ""))
    assert set(getattr(module, "_LAZY", {})).isdisjoint(module.__all__)
