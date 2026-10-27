"""Dry run: a Command that says, in itself, that it only says what it would do — answered by the
handler's `preview`, through the same guards, and never by `execute`."""

from datetime import timedelta

import pytest

from sincpro_framework import (
    ApplicationService,
    DryRunnable,
    Feature,
    Preview,
    UseFramework,
)
from sincpro_framework.context import InMemoryContexts
from sincpro_framework.data_layer.caching import IdempotencyOutcome
from sincpro_framework.entrypoints.services.scalar_execution import ScalarExecution
from sincpro_framework.sincpro_abstractions import DataTransferObject


class CommandCreateQuotation(DryRunnable):
    customer: str
    total: int


class ResponseQuotation(DataTransferObject):
    quotation_id: str


class CommandCreateContact(DryRunnable):
    name: str


class ResponseContact(DataTransferObject):
    contact_id: str


class CommandSell(DryRunnable):
    customer: str
    total: int


class ResponseSold(DataTransferObject):
    contact_id: str
    quotation_id: str


class NoCredit(Exception):
    pass


def sales_bus(written: list[str]) -> UseFramework:
    """A bus whose writes may be run dry — `written` is what reached the customer's system."""
    bus = UseFramework("sales-dry-run", log_after_execution=False)
    bus.store_context_in(InMemoryContexts())

    @bus.feature(CommandCreateQuotation)
    @bus.idempotency(expires_after=timedelta(minutes=2))
    class CreateQuotation(Feature):
        def preview(self, dto: CommandCreateQuotation) -> Preview:
            return Preview(text=f"Quotation for {dto.customer}, Bs {dto.total}")

        def execute(self, dto: CommandCreateQuotation) -> ResponseQuotation:
            written.append(f"quotation {dto.customer}")
            return ResponseQuotation(quotation_id=f"Q-{len(written)}")

    @bus.feature(CommandCreateContact)
    class CreateContact(Feature):
        def preview(self, dto: CommandCreateContact) -> Preview:
            return Preview(text=f"Contact {dto.name}", data={"name": dto.name})

        def execute(self, dto: CommandCreateContact) -> ResponseContact:
            written.append(f"contact {dto.name}")
            return ResponseContact(contact_id=f"C-{dto.name}")

    @bus.app_service(CommandSell)
    class Sell(ApplicationService):
        def preview(self, dto: CommandSell) -> Preview:
            contact = self.feature_bus(
                CommandCreateContact(name=dto.customer, dry_run=True), Preview
            )
            quotation = self.feature_bus(
                CommandCreateQuotation(customer=dto.customer, total=dto.total, dry_run=True),
                Preview,
            )
            return Preview(text=f"{contact.text}; then {quotation.text}")

        def execute(self, dto: CommandSell) -> ResponseSold:
            contact = self.feature_bus(
                CommandCreateContact(name=dto.customer), ResponseContact
            )
            quotation = self.feature_bus(
                CommandCreateQuotation(customer=dto.customer, total=dto.total),
                ResponseQuotation,
            )
            return ResponseSold(
                contact_id=contact.contact_id, quotation_id=quotation.quotation_id
            )

    return bus


def test_a_dry_run_says_what_it_would_do_and_does_nothing():
    written: list[str] = []
    bus = sales_bus(written)

    answer = bus(CommandCreateQuotation(customer="Juan", total=1150, dry_run=True), Preview)

    assert answer == Preview(text="Quotation for Juan, Bs 1150") and written == []


def test_the_same_command_without_it_runs():
    written: list[str] = []
    bus = sales_bus(written)

    done = bus(CommandCreateQuotation(customer="Juan", total=1150), ResponseQuotation)

    assert done.quotation_id == "Q-1" and written == ["quotation Juan"]


def test_a_dry_run_goes_through_the_same_guards_as_the_run():
    written: list[str] = []
    bus = sales_bus(written)

    @bus.interceptor(CommandCreateQuotation)
    def credit_check(dto, call_next):
        if dto.total > 1000:
            raise NoCredit(dto.customer)
        return call_next(dto)

    with pytest.raises(NoCredit):
        bus(CommandCreateQuotation(customer="Juan", total=1150, dry_run=True))
    assert written == []


def test_an_application_service_runs_its_features_dry_to_say_what_it_would_do():
    written: list[str] = []
    bus = sales_bus(written)

    previewed = bus(CommandSell(customer="Juan", total=1150, dry_run=True), Preview)
    sold = bus(CommandSell(customer="Juan", total=1150), ResponseSold)

    assert previewed.text == "Contact Juan; then Quotation for Juan, Bs 1150"
    assert written == ["contact Juan", "quotation Juan"]
    assert (sold.contact_id, sold.quotation_id) == ("C-Juan", "Q-2")


def test_a_dry_run_is_never_stored_and_the_run_is_once():
    written: list[str] = []
    bus = sales_bus(written)

    with bus.context({"tenant_id": "acme", "user_id": "ana"}):
        bus(CommandCreateQuotation(customer="Juan", total=1150, dry_run=True))
        with bus.idempotency.outcomes() as seen:
            first = bus(CommandCreateQuotation(customer="Juan", total=1150))
            again = bus(CommandCreateQuotation(customer="Juan", total=1150))

    assert first == again and written == ["quotation Juan"]
    assert seen == [IdempotencyOutcome.CLAIMED, IdempotencyOutcome.REPLAYED]


def test_a_handler_with_no_preview_gets_the_dry_run_and_decides():
    bus = UseFramework("sales-dry-run-by-hand", log_after_execution=False)

    @bus.feature(CommandCreateContact)
    class CreateContact(Feature):
        def execute(self, dto: CommandCreateContact) -> ResponseContact | Preview:
            if dto.dry_run:
                return Preview(text=f"Contact {dto.name}")
            return ResponseContact(contact_id=f"C-{dto.name}")

    assert bus(CommandCreateContact(name="Ana", dry_run=True)) == Preview(text="Contact Ana")


def test_a_project_previews_its_writes_by_default_on_its_own_base():
    class WriteCommand(DryRunnable):
        dry_run: bool = True

    class CommandSend(WriteCommand):
        to: str

    bus = UseFramework("mail-dry-by-default", log_after_execution=False)
    sent: list[str] = []

    @bus.feature(CommandSend)
    class Send(Feature):
        def preview(self, dto: CommandSend) -> Preview:
            return Preview(text=f"Mail to {dto.to}")

        def execute(self, dto: CommandSend) -> None:
            sent.append(dto.to)

    assert bus(CommandSend(to="ana@acme.bo")) == Preview(text="Mail to ana@acme.bo")
    bus(CommandSend(to="ana@acme.bo", dry_run=False))
    assert sent == ["ana@acme.bo"]


def test_a_wire_carries_it_as_any_field():
    written: list[str] = []
    bus = sales_bus(written)

    answer = ScalarExecution(bus).runner(CommandCreateQuotation)(
        {"customer": "Juan", "total": 1150, "dry_run": True}
    )

    assert answer == {"text": "Quotation for Juan, Bs 1150", "data": {}} and written == []
