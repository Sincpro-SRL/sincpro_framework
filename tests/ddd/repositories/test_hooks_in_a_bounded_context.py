"""Hooks wired the way a bounded context is: `dependencies.py`, `framework.py`, `services/hooks/`,
and a package that re-exports its bases — so a hook imports `Hook` from the context, as a Feature
imports `Feature`.

The layout is written to disk and imported, as a project imports it. The collection is read the
first time a moment fires, never while the repository is built, so every wiring below works: the
repository inside `register_dependencies()`, at the top of `dependencies.py`, and a package that
re-exports `Hook` only after it built its bus.
"""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

DOMAIN = """
from dataclasses import dataclass

from sincpro_framework.ddd import Entity


@dataclass
class Invoice(Entity):
    number: str = ""
    total: int = 0
"""

DEPENDENCIES = """
from typing import TypedDict

from sincpro_framework import UseFramework
from sincpro_framework.ddd import MemoryRepository

from {name}.services.hooks import billing_hooks


class Numbering:
    def __init__(self) -> None:
        self.last = 100

    def next(self) -> str:
        self.last += 1
        return f"F-{{self.last}}"


class DependencyContextType:
    numbering: Numbering
    repository: MemoryRepository


class BillingContext(TypedDict, total=False):
    user_id: str

{repository_at_the_top}
def register_dependencies(framework: UseFramework[DependencyContextType]) -> None:
    framework.add_dependency("numbering", Numbering())
    billing_hooks.inject(framework)
    framework.add_dependency("repository", {repository})
"""

FRAMEWORK = """
from sincpro_framework import Feature as _Feature
from sincpro_framework import UseFramework
from sincpro_framework.ddd import Hook as _Hook

from .dependencies import BillingContext, DependencyContextType, register_dependencies


class Feature(_Feature, DependencyContextType):
    pass


class Hook(_Hook[BillingContext], DependencyContextType):
    pass


def config_framework(name: str) -> UseFramework[DependencyContextType]:
    instance = UseFramework[DependencyContextType](name, log_after_execution=False)
    register_dependencies(instance)
    return instance
"""

HOOKS_PACKAGE = """
from sincpro_framework.ddd import Hooks

billing_hooks = Hooks()
"""

A_HOOK = """
from {name} import Hook
from {name}.domain import Invoice
from {name}.services.hooks import billing_hooks


@billing_hooks.on(Invoice)
class NumbersInvoices(Hook):
    def before_create(self, invoice: Invoice) -> None:
        invoice.number = f"{{self.numbering.next()}} by {{self.context.get('user_id')}}"
"""

A_FEATURE = """
from sincpro_framework import DataTransferObject

from {name} import Feature, billing
from {name}.domain import Invoice


class CommandIssueInvoice(DataTransferObject):
    total: int


class ResponseIssueInvoice(DataTransferObject):
    number: str


@billing.feature(CommandIssueInvoice)
class IssueInvoice(Feature):
    def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
        invoice = Invoice(total=dto.total)
        self.repository.save(invoice)
        return ResponseIssueInvoice(number=invoice.number)
"""

BASES_FIRST = """
from {name}.infrastructure.framework import Feature, Hook, config_framework

billing = config_framework("billing")

from {name}.services import issue_invoice  # noqa: E402, F401
"""

BUS_FIRST = """
from {name}.infrastructure.framework import config_framework

billing = config_framework("billing")

from {name}.infrastructure.framework import Feature, Hook  # noqa: E402
from {name}.services import issue_invoice  # noqa: E402, F401
"""

WIRINGS = {
    "repository inside register_dependencies": (
        "",
        "MemoryRepository(hooks=billing_hooks)",
        BASES_FIRST,
    ),
    "repository at the top of dependencies.py": (
        "\nREPOSITORY = MemoryRepository(hooks=billing_hooks)\n\n",
        "REPOSITORY",
        BASES_FIRST,
    ),
    "the package re-exports Hook after building its bus": (
        "",
        "MemoryRepository(hooks=billing_hooks)",
        BUS_FIRST,
    ),
}


def _write_context(root: Path, name: str, wiring: str) -> None:
    repository_at_the_top, repository, bootstrap = WIRINGS[wiring]
    package = root / name
    for folder in ("infrastructure", "services/hooks", "domain"):
        (package / folder).mkdir(parents=True)
    files = {
        "infrastructure/__init__.py": "",
        "services/__init__.py": "",
        "domain/__init__.py": DOMAIN,
        "infrastructure/dependencies.py": DEPENDENCIES.format(
            name=name, repository_at_the_top=repository_at_the_top, repository=repository
        ),
        "infrastructure/framework.py": FRAMEWORK,
        "services/hooks/__init__.py": HOOKS_PACKAGE,
        "services/hooks/invoices.py": A_HOOK.format(name=name),
        "services/issue_invoice.py": A_FEATURE.format(name=name),
        "__init__.py": bootstrap.format(name=name),
    }
    for path, source in files.items():
        (package / path).write_text(source)


@pytest.fixture
def context_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in [one for one in sys.modules if one.startswith("billing_context")]:
        del sys.modules[name]


@pytest.mark.parametrize("wiring", list(WIRINGS))
def test_a_bounded_context_wires_its_hooks_however_its_modules_are_ordered(
    context_on_disk: Path, wiring: str
):
    """The walk finds the hook the first time a Feature saves, when every module has loaded:
    the hook reads its dependency and the request's context, whatever the wiring."""
    name = f"billing_context_{list(WIRINGS).index(wiring)}"
    _write_context(context_on_disk, name, wiring)

    context = importlib.import_module(name)
    feature = importlib.import_module(f"{name}.services.issue_invoice")

    with context.billing.context({"user_id": "ana"}):
        answer = context.billing(
            feature.CommandIssueInvoice(total=50), feature.ResponseIssueInvoice
        )

    assert answer.number == "F-101 by ana"
