"""The SQLAlchemy adapter's layers point one way: a layer imports only the ones below it, so the
structure keeps saying what each piece is.

    entrypoint      →  services, infrastructure, domain        the facade
    services        →  services, infrastructure, domain
    infrastructure  →  infrastructure, domain
    domain          →  domain

Inside `services/`, the workflows orchestrate the atomic services as an ApplicationService
orchestrates Features: an atomic service never imports a workflow, and a workflow imports no other
workflow but the `Store` they share.
"""

import ast
from pathlib import Path

import pytest

ADAPTER = (
    Path(__file__).parents[3] / "sincpro_framework" / "data_layer" / "orm" / "sqlalchemy"
)
PACKAGE = "sincpro_framework.data_layer.orm.sqlalchemy"
ALLOWED = {
    "entrypoint": {"entrypoint", "services", "infrastructure", "domain"},
    "services": {"services", "infrastructure", "domain"},
    "infrastructure": {"infrastructure", "domain"},
    "domain": {"domain"},
}


WORKFLOWS = "services.workflows"
SHARED_BY_WORKFLOWS = {"store"}


def _imported(module: Path) -> set[str]:
    """Every module of the adapter this one imports, as a path under it: `services.cascade`."""
    imported: set[str] = set()
    for node in ast.walk(ast.parse(module.read_text())):
        names = []
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        imported |= {
            n.removeprefix(f"{PACKAGE}.") for n in names if n.startswith(f"{PACKAGE}.")
        }
    return imported


def _layers_imported(module: Path) -> set[str]:
    return {name.split(".")[0] for name in _imported(module)} & set(ALLOWED)


@pytest.mark.parametrize("layer", sorted(ALLOWED))
def test_a_layer_imports_only_the_layers_below_it(layer):
    wrong = {
        module.name: sorted(_layers_imported(module) - ALLOWED[layer])
        for module in (ADAPTER / layer).rglob("*.py")
    }
    assert {name: layers for name, layers in wrong.items() if layers} == {}


def test_every_module_of_the_adapter_lives_in_a_layer_or_is_a_facade():
    """A file at the adapter's root is a facade: it only gathers, by context, what the layers
    expose — never code of its own."""
    loose = sorted(
        module.name
        for module in ADAPTER.glob("*.py")
        if module.name != "__init__.py"
        and not all(
            isinstance(node, (ast.Import, ast.ImportFrom))
            or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
            for node in ast.parse(module.read_text()).body
        )
    )
    assert loose == []


def test_an_atomic_service_never_imports_a_workflow():
    wrong = {
        module.name: sorted(n for n in _imported(module) if n.startswith(WORKFLOWS))
        for module in (ADAPTER / "services").glob("*.py")
    }
    assert {name: found for name, found in wrong.items() if found} == {}


def test_a_workflow_imports_no_other_workflow_but_the_store_they_share():
    wrong = {}
    for module in (ADAPTER / "services" / "workflows").glob("*.py"):
        others = (
            {
                n.removeprefix(f"{WORKFLOWS}.").split(".")[0]
                for n in _imported(module)
                if n.startswith(f"{WORKFLOWS}.")
            }
            - SHARED_BY_WORKFLOWS
            - {module.stem}
        )
        if others:
            wrong[module.name] = sorted(others)
    assert wrong == {}
