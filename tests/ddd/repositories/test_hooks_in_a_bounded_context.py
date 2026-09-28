"""Hooks wired the way a bounded context is: `dependencies.py`, `framework.py`, `services/hooks/`.

The layout is written to disk and imported, as a project imports it — the walk of the hooks
package, the base `Hook` of `framework.py`, and the repository registered in
`register_dependencies()` all meet only here. What the README and `docs/persistence/hooks.md`
say about the layout is pinned by these two tests.
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

from ..services.hooks import billing_hooks


class Numbering:
    def __init__(self) -> None:
        self.last = 100

    def next(self) -> str:
        self.last += 1
        return f"F-{self.last}"


class DependencyContextType:
    numbering: Numbering
    repository: MemoryRepository


class BillingContext(TypedDict, total=False):
    user_id: str

{AT_IMPORT}
def register_dependencies(framework: UseFramework[DependencyContextType]) -> None:
    framework.add_dependency("numbering", Numbering())
    billing_hooks.inject(framework)
    framework.add_dependency("repository", MemoryRepository(hooks=billing_hooks))
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
from ...domain import Invoice
from ...infrastructure.framework import Hook
from . import billing_hooks


@billing_hooks.on(Invoice)
class NumbersInvoices(Hook):
    def before_create(self, invoice: Invoice) -> None:
        invoice.number = f"{self.numbering.next()} by {self.context.get('user_id')}"
"""

A_FEATURE = """
from sincpro_framework import DataTransferObject

from .. import billing
from ..domain import Invoice
from ..infrastructure.framework import Feature


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

BOOTSTRAP = """
from .infrastructure.framework import config_framework

billing = config_framework("billing")

from .services import issue_invoice  # noqa: E402, F401
"""


def _write_context(root: Path, name: str, at_import: str) -> None:
    package = root / name
    for folder in ("infrastructure", "services/hooks", "domain"):
        (package / folder).mkdir(parents=True)
    (package / "infrastructure" / "__init__.py").write_text("")
    (package / "services" / "__init__.py").write_text("")
    (package / "domain" / "__init__.py").write_text(DOMAIN)
    (package / "infrastructure" / "dependencies.py").write_text(
        DEPENDENCIES.replace("{AT_IMPORT}", at_import)
    )
    (package / "infrastructure" / "framework.py").write_text(FRAMEWORK)
    (package / "services" / "hooks" / "__init__.py").write_text(HOOKS_PACKAGE)
    (package / "services" / "hooks" / "invoices.py").write_text(A_HOOK)
    (package / "services" / "issue_invoice.py").write_text(A_FEATURE)
    (package / "__init__.py").write_text(BOOTSTRAP)


@pytest.fixture
def context_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in [one for one in sys.modules if one.startswith("billing_context")]:
        del sys.modules[name]


def test_the_layout_of_a_bounded_context_wires_its_hooks(context_on_disk: Path):
    """The repository registered in `register_dependencies()`: the walk finds the hook, the
    Feature's save fires it, and it reads its dependency and the request's context."""
    _write_context(context_on_disk, "billing_context_ok", at_import="")

    context = importlib.import_module("billing_context_ok")
    feature = importlib.import_module("billing_context_ok.services.issue_invoice")

    with context.billing.context({"user_id": "ana"}):
        answer = context.billing(
            feature.CommandIssueInvoice(total=50), feature.ResponseIssueInvoice
        )

    assert answer.number == "F-101 by ana"


def test_a_repository_built_at_the_top_of_dependencies_is_the_circular_import_the_docs_warn_of(
    context_on_disk: Path,
):
    """Built at import, reading the collection imports the hook modules while `framework.py`
    is still importing `dependencies.py` — the one wiring the docs tell a project not to use.
    """
    _write_context(
        context_on_disk,
        "billing_context_at_import",
        at_import="\nREPOSITORY = MemoryRepository(hooks=billing_hooks)\n\n",
    )

    with pytest.raises(ImportError, match="partially initialized module"):
        importlib.import_module("billing_context_at_import")
