"""The form hints an entity publishes: what a screen shows read-only, asks for, starts with,
and shows only while something holds.

They are defaults for whoever builds a client, never rules: a component that says otherwise
wins, and the framework refuses nothing because of them. The cases below are an invoicing
screen — a draft and a confirmed invoice, paid in cash or by card — read the way a client reads
it: the definition as JSON, each condition rebuilt and evaluated over the record.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

import pytest

from sincpro_framework import ProgrammingError, UseFramework
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    AllHold,
    AnyHolds,
    EntityReads,
    Get,
    Is,
    Presentation,
    ResponseRecord,
    When,
    presentation_of,
)
from sincpro_framework.ddd.criteria import Operator, expression_from
from sincpro_framework.ddd.criteria.evaluate import matches
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.entity_meta import Meta, describe_class
from sincpro_framework.ddd.exceptions import ConstraintViolation
from sincpro_framework.ddd.repositories import Hook, Hooks


class InvoiceState(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class Payment(StrEnum):
    CASH = "cash"
    CARD = "card"
    TRANSFER = "transfer"


@dataclass
class Partner(Entity):
    name: str
    city: str = ""
    email: str | None = None


@dataclass
class Invoice(Entity):
    partner_id: str
    number: str = "/"
    state: InvoiceState = InvoiceState.DRAFT
    payment: Payment = Payment.CASH
    card_reference: str = ""
    bank_account: str = ""
    currency_id: str = "BOB"
    amount: Decimal = Decimal("0")
    notes: list[str] = field(default_factory=list)

    @classmethod
    def DEFAULT_DISPLAY(cls) -> str:
        return "number"

    presentation = Presentation["Invoice"](
        readonly=lambda i: (i.number, i.state),
        readonly_when=lambda i: (
            When(Is(i.state, Operator.NE, InvoiceState.DRAFT), i.partner_id, i.amount),
        ),
        required_when=lambda i: (
            When(Is(i.payment, Operator.EQ, Payment.CARD), i.card_reference),
            When(Is(i.payment, Operator.EQ, Payment.TRANSFER), i.bank_account),
        ),
        visible_when=lambda i: (
            When(Is(i.payment, Operator.EQ, Payment.CARD), i.card_reference),
            When(Is(i.payment, Operator.EQ, Payment.TRANSFER), i.bank_account),
        ),
    )


def as_the_client_reads(meta: Meta) -> dict[str, dict[str, Any]]:
    """The definition the way a screen receives it: JSON, not Python objects."""
    return meta.model_dump(mode="json")["fields"]


def holds(published: dict[str, Any] | None, record: Any) -> bool:
    """What a client does with a published condition: rebuild it, read each value as its
    field's type (an amount travels as the text `"0"`), and evaluate it over the record.
    `None` means no condition: the hint is off."""
    if published is None:
        return False
    read, dropped = describe_class(type(record), "id").accept(expression_from(published))
    assert not dropped, dropped
    return matches(record, read)


# ---------------------------------------------------------------------------------------------
# What is published with nothing declared
# ---------------------------------------------------------------------------------------------


def test_with_nothing_declared_the_structure_says_what_a_form_needs():
    fields = as_the_client_reads(describe_class(Partner, "id"))

    assert [name for name, one in fields.items() if one["readonly"]] == [
        "id",
        "created_at",
        "updated_at",
        "version",
    ]
    assert [name for name, one in fields.items() if one["required"]] == ["name"]
    assert fields["city"]["default"] == ""
    assert fields["email"]["required"] is False
    assert fields["id"]["default"] is None


def test_the_defaults_a_new_invoice_form_starts_with():
    fields = as_the_client_reads(describe_class(Invoice, "id"))

    assert {
        name: one["default"] for name, one in fields.items() if one["default"] is not None
    } == {
        "version": 0,
        "number": "/",
        "state": "draft",
        "payment": "cash",
        "card_reference": "",
        "bank_account": "",
        "currency_id": "BOB",
        "amount": "0",
    }
    assert fields["notes"]["default"] is None


# ---------------------------------------------------------------------------------------------
# A form, simulated: the client evaluates what was published
# ---------------------------------------------------------------------------------------------


def test_a_draft_invoice_lets_the_partner_be_changed_and_a_confirmed_one_greys_it():
    fields = as_the_client_reads(describe_class(Invoice, "id"))
    draft = Invoice(partner_id="p1")
    confirmed = Invoice(partner_id="p1", number="F-0001", state=InvoiceState.CONFIRMED)

    assert holds(fields["partner_id"]["readonly_when"], draft) is False
    assert holds(fields["partner_id"]["readonly_when"], confirmed) is True
    assert holds(fields["amount"]["readonly_when"], confirmed) is True
    assert fields["number"]["readonly"] is True
    assert fields["partner_id"]["required"] is True


def test_the_card_reference_appears_and_is_asked_for_only_when_paying_by_card():
    fields = as_the_client_reads(describe_class(Invoice, "id"))
    cash = Invoice(partner_id="p1")
    card = Invoice(partner_id="p1", payment=Payment.CARD)
    transfer = Invoice(partner_id="p1", payment=Payment.TRANSFER)

    shown = {
        record.payment: [
            name
            for name, one in fields.items()
            if one["visible_when"] and holds(one["visible_when"], record)
        ]
        for record in (cash, card, transfer)
    }
    asked = {
        record.payment: [
            name for name, one in fields.items() if holds(one["required_when"], record)
        ]
        for record in (cash, card, transfer)
    }

    assert shown == {"cash": [], "card": ["card_reference"], "transfer": ["bank_account"]}
    assert asked == {"cash": [], "card": ["card_reference"], "transfer": ["bank_account"]}


def test_a_field_without_a_visibility_condition_is_always_shown():
    fields = as_the_client_reads(describe_class(Invoice, "id"))

    assert fields["partner_id"]["visible_when"] is None
    assert fields["amount"]["visible_when"] is None


def test_what_the_client_evaluates_from_json_agrees_with_the_server_on_every_record():
    meta = describe_class(Invoice, "id")
    published = as_the_client_reads(meta)
    records = [
        Invoice(partner_id="p", state=state, payment=payment)
        for state in InvoiceState
        for payment in Payment
    ]

    for name, one in meta.fields.items():
        for hint in ("readonly_when", "required_when", "visible_when"):
            on_server = getattr(one, hint)
            for record in records:
                assert holds(published[name][hint], record) == (
                    on_server is not None and matches(record, on_server)
                ), (name, hint, record.state, record.payment)


# ---------------------------------------------------------------------------------------------
# Composing conditions
# ---------------------------------------------------------------------------------------------


@dataclass
class Order(Entity):
    customer_id: str
    state: InvoiceState = InvoiceState.DRAFT
    payment: Payment = Payment.CASH
    discount: Decimal = Decimal("0")
    discount_reason: str = ""

    presentation = Presentation["Order"](
        required_when=lambda o: (
            When(
                AllHold(
                    Is(o.state, Operator.EQ, InvoiceState.DRAFT),
                    Is(o.discount, Operator.GT, Decimal("0")),
                ),
                o.discount_reason,
            ),
        ),
        readonly_when=lambda o: (
            When(
                AnyHolds(
                    Is(o.state, Operator.EQ, InvoiceState.CONFIRMED),
                    Is(o.state, Operator.EQ, InvoiceState.CANCELLED),
                ),
                o.discount,
            ),
            When(Is(o.payment, Operator.EQ, Payment.CARD), o.discount),
        ),
    )


def test_a_reason_is_asked_only_for_a_discount_on_a_draft():
    fields = as_the_client_reads(describe_class(Order, "id"))
    reason = fields["discount_reason"]["required_when"]

    assert holds(reason, Order(customer_id="c", discount=Decimal("10"))) is True
    assert holds(reason, Order(customer_id="c")) is False
    assert (
        holds(
            reason,
            Order(customer_id="c", discount=Decimal("10"), state=InvoiceState.CONFIRMED),
        )
        is False
    )


def test_a_field_covered_twice_is_covered_while_either_holds():
    fields = as_the_client_reads(describe_class(Order, "id"))
    discount = fields["discount"]["readonly_when"]

    assert holds(discount, Order(customer_id="c")) is False
    assert holds(discount, Order(customer_id="c", state=InvoiceState.CANCELLED)) is True
    assert holds(discount, Order(customer_id="c", payment=Payment.CARD)) is True


# ---------------------------------------------------------------------------------------------
# Hints, never rules
# ---------------------------------------------------------------------------------------------


def test_nothing_refuses_a_write_because_of_a_hint():
    repository = MemoryRepository()
    invoice = Invoice(partner_id="p1", number="F-0001", state=InvoiceState.CONFIRMED)
    repository.save(invoice)

    invoice.partner_id = "p2"
    invoice.payment = Payment.CARD
    invoice.number = "F-0002"
    repository.save(invoice)

    stored = repository.get(Invoice, invoice.id)
    assert stored is not None
    assert (stored.partner_id, stored.card_reference, stored.number) == ("p2", "", "F-0002")


def test_a_project_that_wants_a_rule_writes_its_hook_and_reads_the_same_condition():
    """The recipe: the server keeps what the form shows by reading the published condition,
    so the rule is written once."""
    hooks = Hooks(None).inject(UseFramework("billing-hints", log_after_execution=False))

    @hooks.on(Invoice)
    class PaymentNeedsItsReference(Hook):
        def before_save(self, invoice: Invoice) -> None:
            for name, condition in presentation_of(Invoice).required_when.items():
                if matches(invoice, condition) and not getattr(invoice, name):
                    raise ConstraintViolation(f"{name} is required for this invoice")

    repository = MemoryRepository(hooks=hooks)

    repository.save(Invoice(partner_id="p1"))
    repository.save(
        Invoice(partner_id="p1", payment=Payment.CARD, card_reference="**** 4242")
    )
    with pytest.raises(ConstraintViolation, match="card_reference"):
        repository.save(Invoice(partner_id="p1", payment=Payment.CARD))
    with pytest.raises(ConstraintViolation, match="bank_account"):
        repository.save(Invoice(partner_id="p1", payment=Payment.TRANSFER))


# ---------------------------------------------------------------------------------------------
# Overriding, and what is refused when the class is read
# ---------------------------------------------------------------------------------------------


def test_a_subclass_declares_its_own_hints():
    @dataclass
    class CashSale(Invoice):
        presentation = Presentation["CashSale"](readonly=lambda s: (s.payment,))

    fields = as_the_client_reads(describe_class(CashSale, "id"))

    assert fields["payment"]["readonly"] is True
    assert fields["number"]["readonly"] is False
    assert fields["card_reference"]["visible_when"] is None


def test_a_condition_on_a_field_that_does_not_exist_is_refused_when_the_class_is_read():
    @dataclass
    class Broken(Entity):
        state: str = ""

        presentation = Presentation["Broken"](
            visible_when=lambda b: (When(Is(b.stat, Operator.EQ, "x"), b.state),),  # type: ignore[attr-defined]
        )

    with pytest.raises(ProgrammingError, match="stat"):
        describe_class(Broken, "id")


def test_a_condition_written_as_a_bare_value_is_refused_showing_the_form():
    @dataclass
    class Loose(Entity):
        state: str = ""
        notes: str = ""

        presentation = Presentation["Loose"](
            readonly_when=lambda l: (When(l.state == "done", l.notes),),
        )

    with pytest.raises(ProgrammingError, match="Is, AllHold or AnyHolds"):
        describe_class(Loose, "id")


def test_a_hint_list_that_is_not_made_of_when_is_refused():
    @dataclass
    class Mixed(Entity):
        state: str = ""

        presentation = Presentation["Mixed"](
            visible_when=lambda m: (m.state,),  # type: ignore[arg-type,return-value]
        )

    with pytest.raises(ProgrammingError, match="takes When"):
        describe_class(Mixed, "id")


# ---------------------------------------------------------------------------------------------
# What a screen receives through the bus
# ---------------------------------------------------------------------------------------------


class ResponseInvoice(ResponseRecord):
    invoice: Invoice


class QueryGetInvoice(Get[Invoice, ResponseInvoice]):
    pass


def test_the_screen_gets_the_record_and_its_hints_in_one_answer():
    bus = UseFramework("billing-screen", log_after_execution=False)
    confirmed = Invoice(partner_id="p1", number="F-0001", state=InvoiceState.CONFIRMED)
    bus.add_dependency("repository", MemoryRepository().add(confirmed))

    @bus.feature(QueryGetInvoice)
    class InvoiceReads(EntityReads[Invoice]):
        pass

    answer = bus(QueryGetInvoice(id=confirmed.id), ResponseInvoice).model_dump(mode="json")
    fields = answer["entity_meta_data"]["fields"]

    assert answer["invoice"]["state"] == "confirmed"
    assert fields["number"]["readonly"] is True
    assert holds(fields["partner_id"]["readonly_when"], confirmed) is True
    assert fields["card_reference"]["visible_when"] == {
        "field": "payment",
        "operator": "=",
        "value": "card",
    }
