"""Extending a repository's hooks the way a bus's use cases are extended: by reference, never by
a name written as text, and in an order that is always the same.

    @billing_hooks.on(Invoice)                                   ≈ @billing.feature(CommandX)
    @billing_hooks.on(Invoice, replaces=ChecksTotals)            ≈ replaces= on a Feature
    @billing_hooks.on(Invoice, extends=ChecksTotals)             subclass it, super() runs it
                                                                 (replaces= is never a subclass)
    billing_hooks.without(Audits)                                a collection without it
    core_hooks.combined_with(client_hooks)                       a project on top of a core

A hook is always a class: one form, whatever it does.
"""

from dataclasses import dataclass
from typing import Any

import pytest
from structlog.testing import capture_logs

from sincpro_framework import ProgrammingError, UseFramework
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity
from sincpro_framework.ddd.repositories.hooks import Hook, Hooks


@dataclass
class Invoice(Entity):
    total: int = 0


@dataclass
class CreditNote(Entity):
    total: int = 0


def _ran(hooks: Hooks, record: Entity) -> list[str]:
    ran: list[str] = []
    bus = UseFramework("hook-extension", log_after_execution=False)
    bus.add_dependency("ran", ran)
    hooks.inject(bus)
    MemoryRepository(hooks=hooks).save(record)
    return ran


class _Recording(Hook):
    ran: list[str]


def _collection() -> tuple[Hooks, type[Any], type[Any]]:
    hooks = Hooks(None)

    @hooks.on(Invoice)
    class ChecksTotals(_Recording):
        def before_save(self, invoice: Invoice) -> None:
            self.ran.append("checks totals")

    @hooks.on([Invoice, CreditNote])
    class Audits(_Recording):
        def after_save(self, record: Entity) -> None:
            self.ran.append("audits")

    return hooks, ChecksTotals, Audits


def test_the_entity_is_named_where_the_hook_is_registered():
    hooks, _checks, _audits = _collection()

    assert _ran(hooks, Invoice(id="i1")) == ["checks totals", "audits"]
    assert _ran(hooks, CreditNote(id="c1")) == ["audits"]


def test_several_hooks_run_by_before_after_then_sequence_then_registration():
    hooks = Hooks(None)

    @hooks.on(Invoice, sequence=20)
    class Late(_Recording):
        def before_save(self, invoice: Invoice) -> None:
            self.ran.append("late")

    @hooks.on(Invoice)
    class Ordinary(_Recording):
        def before_save(self, invoice: Invoice) -> None:
            self.ran.append("ordinary")

    @hooks.on(Invoice, sequence=5)
    class Early(_Recording):
        def before_save(self, invoice: Invoice) -> None:
            self.ran.append("early")

    @hooks.on(Invoice, sequence=99, before=(Early,))
    class FirstOfAll(_Recording):
        def before_save(self, invoice: Invoice) -> None:
            self.ran.append("first of all")

    assert _ran(hooks, Invoice(id="i1")) == ["first of all", "early", "ordinary", "late"]


def test_a_replacement_runs_in_the_place_of_what_it_replaces():
    hooks, checks, _audits = _collection()

    @hooks.on(Invoice, replaces=checks)
    class ChecksTotalsStrictly(_Recording):
        def before_save(self, invoice: Invoice) -> None:
            self.ran.append("checks strictly")

    assert _ran(hooks, Invoice(id="i1")) == ["checks strictly", "audits"]
    assert hooks.replacements == {
        f"{ChecksTotalsStrictly.__module__}.{ChecksTotalsStrictly.__qualname__}": (
            f"{checks.__module__}.{checks.__qualname__}",
        )
    }


def _warnings(logs: list) -> list[str]:
    return [line["event"] for line in logs if line["log_level"] == "warning"]


def test_a_replacement_that_covers_other_aggregates_runs_with_a_warning():
    hooks, checks, _audits = _collection()

    @hooks.on(CreditNote, replaces=checks)
    class ChecksCreditNotes(_Recording):
        def before_save(self, note: CreditNote) -> None:
            self.ran.append("checks credit notes")

    with capture_logs() as logs:
        assert _ran(hooks, CreditNote(id="c1")) == ["checks credit notes", "audits"]

    assert "covers CreditNote" in _warnings(logs)[0]


def test_extending_a_hook_runs_it_through_super_in_its_place():
    hooks, checks, _audits = _collection()

    @hooks.on(Invoice, extends=checks)
    class ChecksTotalsAndDates(checks):
        def before_save(self, invoice: Invoice) -> None:
            super().before_save(invoice)
            self.ran.append("checks dates")

    assert _ran(hooks, Invoice(id="i1")) == ["checks totals", "checks dates", "audits"]


def test_extending_needs_a_subclass_of_what_it_extends():
    hooks, checks, _audits = _collection()

    with pytest.raises(ProgrammingError, match="subclass"):

        @hooks.on(Invoice, extends=checks)
        class NotASubclass(_Recording):
            def before_save(self, invoice: Invoice) -> None: ...


def test_without_is_a_collection_without_it_and_the_original_stays_whole():
    hooks, _checks, audits = _collection()

    quieter = hooks.without(audits)

    assert _ran(quieter, Invoice(id="i1")) == ["checks totals"]
    assert _ran(hooks, Invoice(id="i2")) == ["checks totals", "audits"]
    assert quieter.switched_off == (f"{audits.__module__}.{audits.__qualname__}",)


def test_a_project_changes_a_core_collection_without_touching_it():
    core, checks, _audits = _collection()
    client = Hooks(None)

    @client.on(Invoice, replaces=checks)
    class LocalTaxCheck(_Recording):
        def before_save(self, invoice: Invoice) -> None:
            self.ran.append("local tax")

    @client.on(Invoice)
    class Notifies(_Recording):
        def after_save(self, invoice: Invoice) -> None:
            self.ran.append("notifies")

    combined = core.combined_with(client)

    assert _ran(combined, Invoice(id="i1")) == ["local tax", "audits", "notifies"]
    assert _ran(core, Invoice(id="i2")) == ["checks totals", "audits"]


def test_a_replacement_that_subclasses_what_it_replaces_works_with_a_warning():
    """Its super() still runs the original: that is extends, and it is said, not refused."""
    hooks, checks, _audits = _collection()

    with capture_logs() as logs:

        @hooks.on(Invoice, replaces=checks)
        class ChecksTotalsSubclassed(checks):
            def before_save(self, invoice: Invoice) -> None:
                super().before_save(invoice)
                self.ran.append("and more")

    assert "that is extends=ChecksTotals" in _warnings(logs)[0]
    assert _ran(hooks, Invoice(id="i1")) == ["checks totals", "and more", "audits"]


def test_replacing_and_extending_at_once_extends_with_a_warning():
    hooks, checks, _audits = _collection()

    with capture_logs() as logs:

        @hooks.on(Invoice, replaces=checks, extends=checks)
        class Both(checks):
            def before_save(self, invoice: Invoice) -> None:
                super().before_save(invoice)

    assert "it extends it" in _warnings(logs)[0]
    assert _ran(hooks, Invoice(id="i1")) == ["checks totals", "audits"]


def test_an_extension_keeps_the_moments_it_does_not_override():
    """Decided per moment: what the subclass does not override is inherited, and still runs."""
    hooks = Hooks(None)

    @hooks.on(Invoice)
    class Numbers(_Recording):
        def before_save(self, invoice: Invoice) -> None:
            self.ran.append("numbers")

        def after_save(self, invoice: Invoice) -> None:
            self.ran.append("announces")

    @hooks.on(Invoice, extends=Numbers)
    class NumbersWhenPositive(Numbers):
        def before_save(self, invoice: Invoice) -> None:
            if invoice.total > 0:
                super().before_save(invoice)

    assert _ran(hooks, Invoice(id="i1", total=5)) == ["numbers", "announces"]
    assert _ran(hooks, Invoice(id="i2", total=0)) == ["announces"]


def test_a_replacement_drops_every_moment_of_what_it_replaces():
    hooks = Hooks(None)

    @hooks.on(Invoice)
    class Numbers(_Recording):
        def before_save(self, invoice: Invoice) -> None:
            self.ran.append("numbers")

        def after_save(self, invoice: Invoice) -> None:
            self.ran.append("announces")

    @hooks.on(Invoice, replaces=Numbers)
    class NumbersBySeries(_Recording):
        def before_save(self, invoice: Invoice) -> None:
            self.ran.append("numbers by series")

    assert _ran(hooks, Invoice(id="i1")) == ["numbers by series"]


def test_replacing_a_hook_that_is_not_in_the_collection_runs_on_its_own_with_a_warning():
    """An extension written for a core hook that is not installed still runs."""
    hooks, _checks, _audits = _collection()

    class Stranger(_Recording):
        def before_save(self, invoice: Invoice) -> None: ...

    @hooks.on(Invoice, replaces=Stranger)
    class ReplacesAStranger(_Recording):
        def before_save(self, invoice: Invoice) -> None:
            self.ran.append("replaces a stranger")

    with capture_logs() as logs:
        ran = _ran(hooks, Invoice(id="i1"))

    assert ran == ["checks totals", "replaces a stranger", "audits"]
    assert "not registered" in _warnings(logs)[0]


def test_switching_off_a_hook_that_is_not_there_does_nothing_with_a_warning():
    hooks, _checks, _audits = _collection()

    class Stranger(_Recording):
        def after_save(self, record: Entity) -> None: ...

    with capture_logs() as logs:
        ran = _ran(hooks.without(Stranger), Invoice(id="i1"))

    assert ran == ["checks totals", "audits"]
    assert "not registered" in _warnings(logs)[0]


def test_a_hook_registered_after_the_repository_is_built_but_before_it_is_used_runs():
    """Lazy, the way the bus is: the collection is read at the first moment that fires, not
    when the repository is built — so the order modules are imported in does not matter."""
    hooks, _checks, _audits = _collection()
    ran: list[str] = []
    bus = UseFramework("hook-extension-lazy", log_after_execution=False)
    bus.add_dependency("ran", ran)
    repository = MemoryRepository(hooks=hooks.inject(bus))

    @hooks.on(Invoice)
    class RegisteredAfterTheRepository(_Recording):
        def before_save(self, invoice: Invoice) -> None:
            self.ran.append("registered after the repository")

    repository.save(Invoice(id="i1"))

    assert ran == ["checks totals", "registered after the repository", "audits"]


def test_a_hook_registered_after_a_repository_used_the_collection_is_refused():
    hooks, _checks, _audits = _collection()
    _ran(hooks, Invoice(id="i1"))

    with pytest.raises(ProgrammingError, match="registered late"):

        @hooks.on(Invoice)
        class TooLate(_Recording):
            def before_save(self, invoice: Invoice) -> None: ...


def test_an_extension_of_an_extension_names_the_latest_and_runs_the_whole_chain():
    hooks, checks, _audits = _collection()

    @hooks.on(Invoice, extends=checks)
    class ChecksDates(checks):
        def before_save(self, invoice: Invoice) -> None:
            super().before_save(invoice)
            self.ran.append("checks dates")

    @hooks.on(Invoice, extends=ChecksDates)
    class ChecksCurrency(ChecksDates):
        def before_save(self, invoice: Invoice) -> None:
            super().before_save(invoice)
            self.ran.append("checks currency")

    assert _ran(hooks, Invoice(id="i1")) == [
        "checks totals",
        "checks dates",
        "checks currency",
        "audits",
    ]
    assert list(hooks.replacements.values()) == [
        (
            f"{checks.__module__}.{checks.__qualname__}",
            f"{ChecksDates.__module__}.{ChecksDates.__qualname__}",
        )
    ]


def test_hooks_that_must_run_before_one_another_are_refused_naming_them():
    hooks = Hooks(None)

    class First(_Recording):
        def before_save(self, invoice: Invoice) -> None: ...

    @hooks.on(Invoice, before=(First,))
    class Second(_Recording):
        def before_save(self, invoice: Invoice) -> None: ...

    hooks.on(Invoice, before=(Second,))(First)
    repository = MemoryRepository(hooks=hooks)

    with pytest.raises(ProgrammingError, match="circle"):
        repository.save(Invoice(id="i1"))
