"""An analysis, not a rule: where another package reaches into a layered package's insides.

    sincpro_framework.context                 its door: what it offers, facades and domain
    sincpro_framework.context.entrypoint...   an explicit path to one facade, when wanted

    from sincpro_framework.context import ContextLevel, context_executions   ->  not reported
    from sincpro_framework.context.entrypoint.scopes import context_scopes   ->  not reported
    from sincpro_framework.context.services.executions import opened_...     ->  reported
    from sincpro_framework.context.domain.level import ContextLevel          ->  reported

Context: `domain/`, `services/`, `adapters/` and `infrastructure/` are how a package does its work;
another package importing them is worth a look — a name missing from the door, or a piece in
the wrong package — but it is not always wrong, so this warns and never fails. Run it alone to
read the list: `pytest tests/runtime/test_package_boundaries.py -W always -rw`.
"""

import ast
import warnings
from pathlib import Path

import pytest

FRAMEWORK = Path(__file__).parents[2] / "sincpro_framework"

LAYERED = {
    "auth": "auth",
    "context": "context",
    "data_layer.caching": "data_layer.caching",
    "data_layer.migrations": "data_layer.migrations",
    "data_layer.orm.sqlalchemy": "data_layer.orm",
    "data_layer.store": "data_layer.store",
    "ddd.criteria": "ddd.criteria",
    "entrypoints": "entrypoints",
    "event_driven": "event_driven",
    "observability.metrics": "observability.metrics",
    "pipelines": "pipelines",
    "remote_execution": "remote_execution",
    "settings": "settings",
}
"""Each package built in layers, and its public door."""

INSIDES = {"domain", "services", "adapters", "infrastructure"}


def _module_name(path: Path) -> str:
    parts = path.relative_to(FRAMEWORK.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _is_within(module: str, package: str) -> bool:
    """`module` is `package` itself, one of its modules, or the door `LAYERED` gives it."""
    full = f"sincpro_framework.{package}"
    door = f"sincpro_framework.{LAYERED[package]}"
    return module == full or module.startswith(f"{full}.") or module == door


def _insides_imported(path: Path, package: str) -> list[str]:
    """Every import in `path` that reaches into a layer of `package` other than `entrypoint`.

    from sincpro_framework.context.services.nodes import live_context  ->  "context.services.nodes"
    """
    prefix = f"sincpro_framework.{package}."
    reached = []
    for node in ast.walk(ast.parse(path.read_text())):
        names = []
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        for name in names:
            if name.startswith(prefix) and name.removeprefix(prefix).split(".")[0] in INSIDES:
                reached.append(name.removeprefix("sincpro_framework."))
                break
    return reached


@pytest.mark.parametrize("package", sorted(LAYERED))
def test_where_a_layered_package_is_entered_by_its_insides(package):
    """Each crossing found is a warning naming the module and what it reached."""
    reached = {
        _module_name(path): _insides_imported(path, package)
        for path in FRAMEWORK.rglob("*.py")
        if not _is_within(_module_name(path), package)
    }
    for module, names in reached.items():
        if names:
            warnings.warn(
                f"{module} reaches into {package}: {', '.join(names)}", stacklevel=1
            )
