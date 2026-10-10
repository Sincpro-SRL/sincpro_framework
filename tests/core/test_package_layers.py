"""The framework's own packages keep their places (PRD_15 §0.1): the entrypoints expose, remote
execution keeps a codebase whole across machines, and both stand on a transport core that exposes
nothing. Neither door imports the other — lazily inside a function included, which is where the
coupling hid before.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "sincpro_framework"


def _imports_of(package: str) -> list[tuple[str, str]]:
    """Every `(file, imported module)` under `package`, at any depth of the file."""
    found: list[tuple[str, str]] = []
    for path in sorted((ROOT / package).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.append((str(path.relative_to(ROOT)), node.module))
            elif isinstance(node, ast.Import):
                found.extend((str(path.relative_to(ROOT)), one.name) for one in node.names)
    return found


@pytest.mark.parametrize(
    ("package", "forbidden"),
    [
        ("entrypoints", "sincpro_framework.remote_execution"),
        ("remote_execution", "sincpro_framework.entrypoints"),
        ("transport", "sincpro_framework.entrypoints"),
        ("transport", "sincpro_framework.remote_execution"),
    ],
)
def test_a_door_never_imports_the_other(package, forbidden):
    crossing = [
        f"{file} imports {module}"
        for file, module in _imports_of(package)
        if module == forbidden or module.startswith(f"{forbidden}.")
    ]

    assert crossing == []
