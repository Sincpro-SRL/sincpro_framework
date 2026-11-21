"""One door per job, each a folder: the root is the contract, `server` verifies what arrives,
`client` presents what goes out, `grants` are roles and groups, `accounts` are the service's own.
Every public name has one door, the root never imports one, and the doors depend on each other
only as stated — what keeps "where is it" answered by the import alone."""

import ast
import importlib
from pathlib import Path

import pytest

AUTH = Path(__file__).parents[2] / "sincpro_framework" / "auth"
DOORS = ("server", "client", "grants", "accounts")
MAY_IMPORT = {"server": set(), "client": set(), "grants": set(), "accounts": {"server"}}
"""Besides the root, which doors each one builds on: the issuer verifies what it issued with the
server's `JwtProvider`."""


def _doors_imported(path: Path) -> set[str]:
    """The doors a module imports, by any absolute import of `sincpro_framework.auth.<door>`."""
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
                if parts[2] in DOORS:
                    found.add(parts[2])
    return found


def _modules_of(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*.py") if "__pycache__" not in path.parts)


def test_the_root_imports_no_door() -> None:
    root_modules = [
        path for path in _modules_of(AUTH) if path.relative_to(AUTH).parts[0] not in DOORS
    ]
    assert {str(path.relative_to(AUTH)): _doors_imported(path) for path in root_modules} == {
        str(path.relative_to(AUTH)): set() for path in root_modules
    }


@pytest.mark.parametrize("door", DOORS)
def test_a_door_builds_on_the_root_and_only_the_doors_it_states(door: str) -> None:
    reached = {
        str(path.relative_to(AUTH)): _doors_imported(path) - {door} - MAY_IMPORT[door]
        for path in _modules_of(AUTH / door)
    }
    assert {module: doors for module, doors in reached.items() if doors} == {}


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
        for name in module.__all__:
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
        assert outside == {}, door
