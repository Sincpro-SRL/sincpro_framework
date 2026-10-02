"""The events module's layers point one way: a layer imports only the ones below it, so the tree
keeps saying what each piece is.

    entrypoint      →  services, adapters, infrastructure, domain     what a project holds
    adapters        →  services, infrastructure, domain               queues, in-memory stores
    services        →  services, infrastructure, domain               the subscriber, a pass
    infrastructure  →  infrastructure, domain                         the trace
    domain          →  domain                                         the vocabulary and ports

Nothing lives at the root but `__init__.py`: an optional extra is an adapter like any other
(`adapters/faststream/`, guarded at import), the way `caching.adapters.redis` is.
"""

import ast
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[2] / "sincpro_framework" / "event_driven"
PACKAGE = "sincpro_framework.event_driven"
ALLOWED = {
    "entrypoint": {"entrypoint", "services", "adapters", "infrastructure", "domain"},
    "adapters": {"adapters", "services", "infrastructure", "domain"},
    "services": {"services", "infrastructure", "domain"},
    "infrastructure": {"infrastructure", "domain"},
    "domain": {"domain"},
}


def _layers_imported(module: Path) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(module.read_text())):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        imported |= {
            name.removeprefix(f"{PACKAGE}.").split(".")[0]
            for name in names
            if name.startswith(f"{PACKAGE}.")
        }
    return imported & set(ALLOWED)


@pytest.mark.parametrize("layer", sorted(ALLOWED))
def test_a_layer_imports_only_the_layers_below_it(layer):
    wrong = {
        module.name: sorted(_layers_imported(module) - ALLOWED[layer])
        for module in (MODULE / layer).rglob("*.py")
    }
    assert {name: layers for name, layers in wrong.items() if layers} == {}


def test_every_module_lives_in_a_layer():
    loose = sorted(one.name for one in MODULE.glob("*.py") if one.name != "__init__.py")
    packages = sorted(
        one.name
        for one in MODULE.iterdir()
        if one.is_dir() and one.name not in {*ALLOWED, "__pycache__"}
    )
    assert (loose, packages) == ([], [])


def test_the_domain_holds_no_third_party_import():
    """The vocabulary is the stdlib's and the framework's: a broker's library belongs to its
    adapter, behind its extra."""
    third_party = set()
    for module in (MODULE / "domain").rglob("*.py"):
        for node in ast.walk(ast.parse(module.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                root = node.module.split(".")[0]
                if root not in {
                    "sincpro_framework",
                    "abc",
                    "collections",
                    "dataclasses",
                    "datetime",
                    "enum",
                    "typing",
                    "threading",
                }:
                    third_party.add((module.name, node.module))
    assert third_party == set()
