"""Inside a package built in layers, every layer imports only the layers below it.

    entrypoint      →  services, adapters, infrastructure, domain     the facades
    services        →  adapters, infrastructure, domain               the operations
    adapters        →  infrastructure, domain                         what is external
    infrastructure  →  domain                                         the low-level mechanism
    domain          →  nothing of the package but domain

A layer may import its own files too. Context: the public API calls the entrypoint, the entrypoint
the services, the services the adapters, the infrastructure and the domain — never the other way,
so a piece below never learns about one above and can be read, tested and replaced alone.
"""

import ast
from pathlib import Path

import pytest

FRAMEWORK = Path(__file__).parents[2] / "sincpro_framework"

LAYERED = [
    "auth",
    "context",
    "data_layer/caching",
    "data_layer/object_storage",
    "data_layer/migrations",
    "data_layer/orm/sqlalchemy",
    "data_layer/store",
    "ddd/criteria",
    "entrypoints",
    "event_driven",
    "observability/metrics",
    "pipelines",
    "remote_execution",
    "settings",
]

BELOW = {
    "entrypoint": {"entrypoint", "services", "adapters", "infrastructure", "domain"},
    "services": {"services", "adapters", "infrastructure", "domain"},
    "adapters": {"adapters", "infrastructure", "domain"},
    "infrastructure": {"infrastructure", "domain"},
    "domain": {"domain"},
}
"""What each layer may import of its own package."""


def _wrong_imports(package: str, layer: str) -> dict[str, list[str]]:
    """Each file of `layer` with the imports of its package that reach a layer above it.

    context/adapters/codecs.py importing context.services.travel  ->  {"codecs.py": ["services.travel"]}
    """
    root = FRAMEWORK / package
    module = "sincpro_framework." + package.replace("/", ".")
    wrong: dict[str, list[str]] = {}
    for path in sorted((root / layer).rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.ImportFrom) and node.module):
                continue
            if not node.module.startswith(f"{module}."):
                continue
            inside = node.module.removeprefix(f"{module}.")
            if inside.split(".")[0] in BELOW and inside.split(".")[0] not in BELOW[layer]:
                wrong.setdefault(str(path.relative_to(root)), []).append(inside)
    return wrong


@pytest.mark.parametrize("package", LAYERED)
@pytest.mark.parametrize("layer", sorted(BELOW))
def test_a_layer_imports_only_the_layers_below_it(package, layer):
    assert _wrong_imports(package, layer) == {}
