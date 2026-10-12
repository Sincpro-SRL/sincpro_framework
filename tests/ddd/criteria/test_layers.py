"""The layers of `ddd/criteria/`: `domain` ← `services` ← `entrypoint`, and nothing the other way.

The language knows nothing of how it is evaluated or resolved; the pieces that need a clock or a
calendar know nothing of who calls them. A file that reaches upward is a cycle waiting for the next
import, so it is refused here and not found later.
"""

import ast
from pathlib import Path

import pytest

import sincpro_framework.ddd.criteria as package

ROOT = Path(package.__file__).parent
PREFIX = "sincpro_framework.ddd.criteria."
ABOVE = {
    "domain": ("services", "entrypoint"),
    "services": ("entrypoint",),
    "entrypoint": (),
}


def imported_layers(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith(PREFIX)
        ):
            found.add(node.module.removeprefix(PREFIX).split(".")[0])
    return found


@pytest.mark.parametrize("layer", ABOVE)
def test_a_layer_never_imports_the_ones_above_it(layer):
    reaching = {
        str(path.relative_to(ROOT)): sorted(imported_layers(path) & set(ABOVE[layer]))
        for path in (ROOT / layer).glob("*.py")
        if imported_layers(path) & set(ABOVE[layer])
    }

    assert reaching == {}


def test_nothing_outside_the_package_reaches_into_its_layers():
    outside = {}
    framework = ROOT.parents[1]
    for path in framework.rglob("*.py"):
        if ROOT in path.parents:
            continue
        reached = {
            node.module
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith(PREFIX)
        }
        if reached:
            outside[str(path.relative_to(framework))] = sorted(reached)

    assert outside == {}
