"""The layers of `ddd/metamodel/`: `domain` ← `services` ← `infrastructure` ← `entrypoint`, and
nothing the other way; and the one place that reads what an entity declares.

The model is described by the domain, compiled by a service, held by the infrastructure and handed
out by the entrypoint. A file that reaches upward is a cycle waiting for the next import. And a
module outside the compiler that reads a class's declarations on its own is a second description
waiting to drift from the first — what the metamodel exists to end.
"""

import ast
from pathlib import Path

import pytest

import sincpro_framework.ddd.metamodel as package

ROOT = Path(package.__file__).parent
FRAMEWORK = ROOT.parents[1]
PREFIX = "sincpro_framework.ddd.metamodel."
ABOVE = {
    "domain": ("services", "infrastructure", "entrypoint"),
    "services": ("infrastructure", "entrypoint"),
    "infrastructure": ("entrypoint",),
    "entrypoint": (),
}
READERS = {
    "annotations_of",
    "checks_of",
    "computations_of",
    "computed_fields_of",
    "defaults_of",
    "field_translations",
    "framework_fields",
    "presentation_of",
}
"""What reads a class's declarations: only the compiler calls them, and the vocabulary they belong
to."""
READ_BY = {
    "ddd/metamodel/services/compiler.py",
    "ddd/__init__.py",  # the door: a project reads a presentation through it, the framework never
    "ddd/events/mixins/change_tracking.py",  # vocabulary below the metamodel: an event's labels
    "settings/services/resolution.py",  # settings shapes, not entities
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


def test_only_the_compiler_reads_what_an_entity_declares():
    reading = {}
    for path in FRAMEWORK.rglob("*.py"):
        where = str(path.relative_to(FRAMEWORK))
        if where.startswith("ddd/entity/") or where in READ_BY:
            continue
        names = {
            alias.name
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("sincpro_framework.ddd.entity")
            for alias in node.names
            if alias.name in READERS
        }
        if names:
            reading[where] = sorted(names)

    assert reading == {}
