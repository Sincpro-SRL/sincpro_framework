"""An interceptor written as a class: its dependencies injected as a Feature's are, one made per
generation of the bus — a dependency replaced in `fresh()` reaches it."""

import pytest

from sincpro_framework import DataTransferObject, Feature, Interceptor, UseFramework
from sincpro_framework.bus_pipeline.interceptors import CallNext


class CommandIssueInvoice(DataTransferObject):
    customer: str


class NoCredit(Exception):
    pass


class Credit:
    def __init__(self, allowed: set[str]) -> None:
        self.allowed = allowed

    def check(self, customer: str) -> None:
        if customer not in self.allowed:
            raise NoCredit(customer)


def billing() -> tuple[UseFramework, type[Interceptor]]:
    bus = UseFramework("billing-interceptor-classes", log_after_execution=False)
    bus.add_dependency("credit", Credit({"acme"}))

    @bus.feature(CommandIssueInvoice)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> str:
            return f"issued to {dto.customer}"

    @bus.interceptor(CommandIssueInvoice)
    class CheckCredit(Interceptor):
        credit: Credit

        def intercept(self, dto: CommandIssueInvoice, call_next: CallNext[str]) -> str:
            self.credit.check(dto.customer)
            return call_next(dto)

    return bus, CheckCredit


def test_a_class_interceptor_reads_its_injected_dependencies():
    bus, _ = billing()

    assert bus(CommandIssueInvoice(customer="acme")) == "issued to acme"
    with pytest.raises(NoCredit):
        bus(CommandIssueInvoice(customer="globex"))


def test_a_dependency_replaced_in_a_fresh_bus_reaches_it():
    bus, _ = billing()
    test = bus.fresh()
    test.add_dependency("credit", Credit({"globex"}), replaces=bus.deps.credit)

    assert test(CommandIssueInvoice(customer="globex")) == "issued to globex"
    with pytest.raises(NoCredit):
        bus(CommandIssueInvoice(customer="globex"))


def test_a_class_is_named_by_replaces_and_switched_off():
    bus, check_credit = billing()

    @bus.interceptor(CommandIssueInvoice, replaces=check_credit)
    def lenient(dto, call_next):
        return call_next(dto)

    assert bus(CommandIssueInvoice(customer="globex")) == "issued to globex"

    off, checking = billing()
    off.without_interceptor(checking)
    assert off(CommandIssueInvoice(customer="globex")) == "issued to globex"
