"""import_cycles / layer_violations over small packages written for each case.

Each case is a package on disk shaped like a consumer: a bounded context with its layers,
bootstrapped the way the framework asks (bus first, services after).
"""

import textwrap
from pathlib import Path

import pytest

from sincpro_framework.testing import import_cycles, layer_violations


@pytest.fixture
def write_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    counter = iter(range(1000))

    def write(files: dict[str, str]) -> str:
        name = f"arch_case_{next(counter)}"
        for relative, source in {"__init__.py": "", **files}.items():
            path = tmp_path / name / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            for parent in path.relative_to(tmp_path / name).parents:
                (tmp_path / name / parent / "__init__.py").touch()
            path.write_text(textwrap.dedent(source).replace("PKG", name))
        return name

    return write


BOOTSTRAPPED_CONTEXT = {
    "sales/__init__.py": """
        from PKG.sales.infrastructure.framework import Feature

        sales = object()

        from PKG.sales import services  # noqa: E402
    """,
    "sales/infrastructure/framework.py": "class Feature: ...\n",
    "sales/services/__init__.py": "from PKG.sales.services import create_contact\n",
    "sales/services/create_contact.py": """
        from PKG.sales import Feature, sales
        from PKG.sales.domain.contact import Contact

        class CommandCreateContact: ...
        class ResponseCreateContact: ...
    """,
    "sales/domain/contact.py": "class Contact: ...\n",
}


# ---------------------------------------------------------------------------------------------
# import_cycles
# ---------------------------------------------------------------------------------------------


def test_a_package_without_cycles_reports_none(write_package):
    package = write_package(BOOTSTRAPPED_CONTEXT)

    assert import_cycles(package) == []


def test_two_modules_importing_each_other_are_a_cycle(write_package):
    package = write_package({"a.py": "from PKG import b\n", "b.py": "from PKG.a import x\n"})

    [cycle] = import_cycles(package)

    assert cycle.modules == (f"{package}.a", f"{package}.b")
    assert repr(cycle) == f"{package}.a → {package}.b → {package}.a"


def test_imports_that_do_not_run_at_load_time_are_not_edges(write_package):
    package = write_package(
        {
            "a.py": "from PKG import b\n",
            "b.py": """
                from typing import TYPE_CHECKING

                if TYPE_CHECKING:
                    from PKG.a import x

                def later():
                    from PKG.a import y
            """,
        }
    )

    assert import_cycles(package) == []


def test_bus_bound_after_services_are_imported_is_a_cycle(write_package):
    package = write_package(
        {
            **BOOTSTRAPPED_CONTEXT,
            "sales/__init__.py": """
                from PKG.sales import services

                sales = object()
            """,
        }
    )

    [cycle] = import_cycles(package)

    assert f"{package}.sales" in cycle.modules
    assert f"{package}.sales.services.create_contact" in cycle.modules


def test_a_cycle_through_a_half_loaded_package_is_found(write_package):
    package = write_package(
        {
            "__init__.py": """
                from PKG.domain import codes

                bus = object()

                from PKG import services
            """,
            "domain/codes.py": "from PKG import bus\n",
            "services/use_case.py": "from PKG import bus\n",
        }
    )

    [cycle] = import_cycles(package)

    assert cycle.modules == (package, f"{package}.domain.codes")


def test_the_framework_has_only_its_known_cycle():
    assert [cycle.modules for cycle in import_cycles("sincpro_framework")] == [
        (
            "sincpro_framework.ddd.entity",
            "sincpro_framework.ddd.entity.mixins.tracking",
            "sincpro_framework.ddd.events",
        )
    ]


# ---------------------------------------------------------------------------------------------
# layer_violations
# ---------------------------------------------------------------------------------------------


def _rules(violations) -> list[str]:
    return [violation.rule for violation in violations]


def test_a_context_that_follows_the_conventions_reports_none(write_package):
    package = write_package(BOOTSTRAPPED_CONTEXT)

    assert layer_violations(package) == []


def test_domain_reaching_an_adapter(write_package):
    package = write_package(
        {
            "sales/domain/contact.py": "from PKG.sales.adapters.odoo import Client\n",
            "sales/adapters/odoo.py": "class Client: ...\n",
        }
    )

    [violation] = layer_violations(package)

    assert violation.rule == "domain-is-vocabulary"
    assert violation.importer == f"{package}.sales.domain.contact"
    assert violation.line == 1


def test_an_adapter_importing_another_adapter(write_package):
    package = write_package(
        {
            "sales/adapters/odoo/__init__.py": "from PKG.sales.adapters.odoo.client import C\n",
            "sales/adapters/odoo/client.py": "from PKG.sales.adapters.mail import M\n",
            "sales/adapters/mail.py": "class M: ...\n",
        }
    )

    assert _rules(layer_violations(package)) == ["adapters-are-independent"]


def test_a_service_calling_another_use_case_without_the_bus(write_package):
    package = write_package(
        {
            "sales/services/a.py": "from PKG.sales.services.b import CreateB, build_b, TEMPLATE\n",
            "sales/services/b.py": """
                from PKG.sales import sales

                TEMPLATE = 1

                def build_b(): ...

                @sales.feature(QueryB)
                class CreateB: ...
            """,
        }
    )

    [violation] = layer_violations(package)

    assert violation.rule == "services-reused-through-bus"
    assert "CreateB, build_b" in violation.message and "TEMPLATE" not in violation.message


def test_dtos_are_judged_by_the_bus_not_by_their_names(write_package):
    package = write_package(
        {
            "sales/services/a.py": "from PKG.sales.services.b import QueryB, ResultB, Anything\n",
            "sales/services/b.py": """
                class QueryB: ...
                class ResultB: ...
                class Anything: ...
            """,
        }
    )

    assert layer_violations(package) == []


def test_a_services_package_re_exporting_a_feature_class(write_package):
    package = write_package(
        {
            "sales/services/__init__.py": "from PKG.sales.services.a import CreateA, QueryA\n",
            "sales/services/a.py": """
                from PKG.sales import sales

                class QueryA: ...

                @sales.feature(QueryA)
                class CreateA: ...
            """,
        }
    )

    [violation] = layer_violations(package)

    assert violation.rule == "services-reused-through-bus"
    assert "re-exports CreateA" in violation.message


def test_a_context_calling_an_entrypoint(write_package):
    package = write_package(
        {
            "sales/infrastructure/dependencies.py": "from PKG.entrypoints.queue import pub\n",
            "entrypoints/queue.py": "pub = 1\n",
        }
    )

    assert _rules(layer_violations(package)) == ["entrypoints-are-outermost"]


def test_two_contexts_depending_on_each_other(write_package):
    package = write_package(
        {
            "sales/services/a.py": "from PKG.catalog.domain.product import Product\n",
            "catalog/domain/product.py": "class Product: ...\n",
            "catalog/services/b.py": "from PKG.sales.domain.customer import Customer\n",
            "sales/domain/customer.py": "class Customer: ...\n",
        }
    )

    violations = layer_violations(package)

    assert _rules(violations) == ["contexts-are-acyclic", "contexts-are-acyclic"]
    assert f"{package}.catalog → {package}.sales → {package}.catalog" in violations[0].message


def test_common_reaching_a_sibling_but_not_the_root(write_package):
    package = write_package(
        {
            "domains/common/adapters/auth.py": """
                from PKG.infrastructure.client import Client
                from PKG.domains.sales.domain.customer import Customer
            """,
            "infrastructure/client.py": "class Client: ...\n",
            "domains/sales/domain/customer.py": "class Customer: ...\n",
        }
    )

    [violation] = layer_violations(package)

    assert violation.rule == "common-imports-no-context"
    assert violation.imported == f"{package}.domains.sales.domain.customer"


def test_a_rule_the_consumer_does_not_follow_can_be_ignored(write_package):
    package = write_package(
        {
            "sales/adapters/a.py": "from PKG.sales.adapters.b import B\n",
            "sales/adapters/b.py": "class B: ...\n",
        }
    )

    assert layer_violations(package, ignore=["adapters-are-independent"]) == []


def test_an_unknown_package_is_refused():
    with pytest.raises(ModuleNotFoundError):
        import_cycles("no_such_package_anywhere")
