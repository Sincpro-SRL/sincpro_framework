"""What a published 3.x release exported keeps importing within 3.x — the same object, warned
about with its new home — and a module path never disappears without an alias (#334)."""

import importlib
import pathlib
import warnings

import pytest

from sincpro_framework.auth import AnyOf, Permission
from sincpro_framework.auth.common.infrastructure.moved_names import (
    MOVED_FROM_ROOT,
    MOVED_MODULES,
    MOVED_PACKAGES,
)

PUBLISHED = pathlib.Path(__file__).with_name("published_auth_modules.txt")


class Billing(Permission):
    READ = "billing.invoice.read"
    ADMIN = "billing.invoice.admin"


@pytest.mark.parametrize(
    ("old_module", "name", "new_module", "new_name"),
    [
        (old, name, *where)
        for old, names in MOVED_MODULES.items()
        for name, where in names.items()
    ],
)
def test_an_old_path_answers_the_same_object_and_says_where_it_lives(
    old_module: str, name: str, new_module: str, new_name: str
) -> None:
    with pytest.warns(DeprecationWarning, match=new_module.replace(".", r"\.")):
        found = getattr(importlib.import_module(old_module), name)
    assert found is getattr(importlib.import_module(new_module), new_name)


def test_the_import_3_40_code_wrote_still_works() -> None:
    namespace: dict[str, object] = {}
    with pytest.warns(DeprecationWarning, match="sincpro_framework.auth"):
        exec("from sincpro_framework.auth.domain.identity import IdentityKind", namespace)

    from sincpro_framework.auth import IdentityKind as Current

    assert namespace["IdentityKind"] is Current


@pytest.mark.parametrize(("name", "door"), sorted(MOVED_FROM_ROOT.items()))
def test_a_name_the_root_exported_is_read_from_its_door(name: str, door: str) -> None:
    auth = importlib.import_module("sincpro_framework.auth")

    with pytest.warns(DeprecationWarning, match=door.replace(".", r"\.")):
        found = getattr(auth, name)

    assert found is getattr(importlib.import_module(door), name)


def test_any_of_permissions_is_its_members_with_a_warning() -> None:
    requirement = AnyOf((Billing.READ, Billing.ADMIN))

    with pytest.warns(DeprecationWarning, match="AnyOf.members"):
        assert requirement.permissions == requirement.members


def test_the_current_paths_raise_no_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        from sincpro_framework.auth import AnyOf, IdentityKind  # noqa: F401
        from sincpro_framework.auth.server import StaticProvider  # noqa: F401


def test_an_unknown_name_of_an_old_module_is_still_an_attribute_error() -> None:
    old = importlib.import_module("sincpro_framework.auth.domain.identity")

    with pytest.raises(AttributeError):
        getattr(old, "NeverExisted")


def test_every_module_path_a_3x_release_published_imports_or_is_moved() -> None:
    """`published_auth_modules.txt` lists every `sincpro_framework.auth` module of 3.26 → 3.41:
    deleting or moving one fails here until `MOVED_MODULES` answers it."""
    missing = []
    for module in PUBLISHED.read_text().split():
        if module in MOVED_MODULES or module in MOVED_PACKAGES:
            continue
        try:
            importlib.import_module(module)
        except ModuleNotFoundError:
            missing.append(module)
    assert missing == []
