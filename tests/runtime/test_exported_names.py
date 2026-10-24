"""Every name a package exports resolves.

Context: a package that loads its names lazily — a map from the name to the module it lives in —
answers an import error only when the name is first asked for; a module moved without its map
updated passes every test that never imports it from there. This asks each one.
"""

import importlib
import pkgutil

import pytest

import sincpro_framework


def _packages() -> list[str]:
    """Every package of the framework that imports — one behind an extra not installed is left
    out, as a project without that extra never imports it."""
    found = ["sincpro_framework"]
    for info in pkgutil.walk_packages(sincpro_framework.__path__, "sincpro_framework."):
        if not info.ispkg:
            continue
        try:
            importlib.import_module(info.name)
        except ImportError:
            continue
        found.append(info.name)
    return found


@pytest.mark.parametrize("package", _packages())
def test_every_exported_name_resolves(package: str) -> None:
    """1. The package imported.
    2. Final: each name of its `__all__` read from it — `pkg.store_context_in` answers the
       function, never `ModuleNotFoundError`."""
    module = importlib.import_module(package)
    unresolved = []
    for name in getattr(module, "__all__", []):
        try:
            getattr(module, name)
        except (AttributeError, ImportError) as error:
            unresolved.append(f"{name}: {error}")
    assert not unresolved, unresolved
