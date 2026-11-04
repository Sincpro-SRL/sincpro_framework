"""A form's question answered before anything is saved: computed fields, the preview, and the
guard that makes a preview unable to write.

The cases are an invoicing screen: lines of quantity × price make a subtotal, a discount makes
the total, paying by card asks for the card reference — the computations are the invoice's
own methods, the framework only orders and runs them.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

import pytest

from sincpro_framework import UseFramework, exceptions
from sincpro_framework.common.failures import problem_of
from sincpro_framework.data_layer.repositories import MemoryNumbering, MemoryRepository
from sincpro_framework.ddd import (
    ArchivableMixin,
    Computations,
    Compute,
    Condition,
    Criteria,
    EntityReads,
    Presentation,
    PreviewEntity,
    ResponsePreviewEntity,
    When,
    apply_values,
)
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd import read_only, recompute, warn_form
from sincpro_framework.ddd.criteria import Operator
from sincpro_framework.ddd.editing.preview import warnings_at
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.reads.reads import copied


class Payment(StrEnum):
    CASH = "cash"
    CARD = "card"


@dataclass
class InvoiceLine:
    product: str
    qty: int
    price: Decimal


ran: list[str] = []
numbering = MemoryNumbering()


@dataclass
class Invoice(Entity):
    partner_id: str
    number: str = "/"
    payment: Payment = Payment.CASH
    card_reference: str = ""
    lines: list[InvoiceLine] = field(default_factory=list)
    discount: Decimal = Decimal("0")
    subtotal: Decimal = Decimal("0")
    total: Decimal = Decimal("0")

    def subtotal_of(self) -> Decimal:
        ran.append("subtotal")
        return sum((line.qty * line.price for line in self.lines), Decimal("0"))

    def total_of(self) -> Decimal:
        ran.append("total")
        if self.subtotal and self.discount > self.subtotal * Decimal("0.10"):
            warn_form("a discount above 10% needs approval", field="discount")
        return self.subtotal - self.discount

    computations = Computations["Invoice"](
        lambda i: (
            Compute(i.total, depends=(i.subtotal, i.discount), by=Invoice.total_of),
            Compute(i.subtotal, depends=(i.lines,), by=Invoice.subtotal_of),
        )
    )
    presentation = Presentation["Invoice"](
        readonly=lambda i: (i.number, i.subtotal, i.total),
        required_when=lambda i: (
            When(Condition.on(i.payment, Operator.EQ, Payment.CARD), i.card_reference),
        ),
        visible_when=lambda i: (
            When(Condition.on(i.payment, Operator.EQ, Payment.CARD), i.card_reference),
        ),
    )


class ResponsePreviewInvoice(ResponsePreviewEntity):
    pass


class QueryPreviewInvoice(PreviewEntity[Invoice, ResponsePreviewInvoice]):
    pass


def billing(*invoices: Invoice) -> tuple[UseFramework, MemoryRepository]:
    bus = UseFramework("billing-preview", log_after_execution=False)
    repository = MemoryRepository().add(*invoices)
    bus.add_dependency("repository", repository)

    @bus.feature(QueryPreviewInvoice)
    class InvoiceReads(EntityReads[Invoice]):
        pass

    return bus, repository


def a_stored_invoice() -> Invoice:
    invoice = Invoice(
        partner_id="p1",
        lines=[
            InvoiceLine("cuaderno", 10, Decimal("12.50")),
            InvoiceLine("lapiz", 4, Decimal("2")),
        ],
    )
    recompute(invoice)
    return invoice


# ---------------------------------------------------------------------------------------------
# Computed fields
# ---------------------------------------------------------------------------------------------


def test_the_derivations_run_in_the_order_their_dependencies_need():
    ran.clear()
    invoice = Invoice(
        partner_id="p1", lines=[InvoiceLine("x", 3, Decimal("10"))], discount=Decimal("5")
    )

    moved = recompute(invoice)

    assert ran == ["subtotal", "total"]
    assert moved == ("subtotal", "total")
    assert (invoice.subtotal, invoice.total) == (Decimal("30"), Decimal("25"))


def test_a_change_recomputes_only_what_it_reaches():
    invoice = a_stored_invoice()
    ran.clear()
    invoice.discount = Decimal("10")

    assert recompute(invoice, ("discount",)) == ("total",)
    assert ran == ["total"]

    ran.clear()
    invoice.lines = [InvoiceLine("x", 1, Decimal("1"))]
    recompute(invoice, ("lines",))
    assert ran == ["subtotal", "total"]


def test_a_save_stores_what_the_derivations_compute():
    repository = MemoryRepository()
    invoice = Invoice(partner_id="p1", lines=[InvoiceLine("x", 2, Decimal("7.50"))])

    repository.save(invoice)

    stored = repository.get(Invoice, invoice.id)
    assert stored is not None and stored.total == Decimal("15.00")


def test_an_entity_without_derivations_is_saved_as_it_always_was():
    @dataclass
    class Note(Entity):
        title: str = ""

    note = Note(title="hola")
    assert recompute(note) == ()
    repository = MemoryRepository()
    repository.save(note)
    assert repository.get(Note, note.id) == note


@pytest.mark.parametrize(
    ("declared", "refusal"),
    [
        (
            lambda i: (
                Compute(i.a, depends=(i.b,), by=lambda r: 1),
                Compute(i.b, depends=(i.a,), by=lambda r: 1),
            ),
            "cycle: a → b",
        ),
        (lambda i: (Compute(i.a, depends=(i.a,), by=lambda r: 1),), "a from itself"),
        (
            lambda i: (
                Compute(i.a, depends=(), by=lambda r: 1),
                Compute(i.a, depends=(), by=lambda r: 2),
            ),
            "computes a twice",
        ),
        (lambda i: (Compute(i.c, depends=(), by=lambda r: 1),), "names c"),
        (lambda i: (i.a,), "takes Compute"),
    ],
)
def test_a_derivation_that_cannot_be_ordered_is_refused_when_the_class_is_read(
    declared, refusal
):
    @dataclass
    class Broken(Entity):
        a: int = 0
        b: int = 0

        computations = Computations["Broken"](declared)

    with pytest.raises(exceptions.ProgrammingError, match=refusal):
        recompute(Broken())


# ---------------------------------------------------------------------------------------------
# assign
# ---------------------------------------------------------------------------------------------


def test_assign_reads_each_value_as_its_field_type_and_answers_what_changed():
    invoice = Invoice(partner_id="p1")

    changed = apply_values(
        invoice,
        {
            "discount": "12.50",
            "payment": "card",
            "partner_id": "p1",
            "lines": [{"product": "mochila", "qty": "2", "price": "150.00"}],
        },
    )

    assert changed == ("discount", "payment", "lines")
    assert invoice.discount == Decimal("12.50")
    assert invoice.payment is Payment.CARD
    assert invoice.lines == [InvoiceLine("mochila", 2, Decimal("150.00"))]


def test_assign_refuses_a_name_that_is_not_a_field_and_a_value_the_type_cannot_read():
    invoice = Invoice(partner_id="p1")

    with pytest.raises(exceptions.ClientError, match="no field colour"):
        apply_values(invoice, {"colour": "red"})
    with pytest.raises(exceptions.ClientError, match="Invoice.discount cannot take 'mucho'"):
        apply_values(invoice, {"discount": "mucho"})


def test_assign_refuses_nothing_because_of_a_hint():
    invoice = Invoice(partner_id="p1")

    assert apply_values(invoice, {"number": "F-9", "total": "1"}) == ("number", "total")


# ---------------------------------------------------------------------------------------------
# The preview, through the bus
# ---------------------------------------------------------------------------------------------


def test_a_new_invoice_form_starts_from_the_defaults_and_the_states():
    bus, _ = billing()

    answer = bus(QueryPreviewInvoice(), ResponsePreviewInvoice)

    assert answer.values["number"] == "/"
    assert answer.values["payment"] == "cash"
    assert answer.values["total"] == "0"
    assert answer.values["partner_id"] is None
    assert answer.states["partner_id"].required is True
    assert answer.states["total"].readonly is True
    assert answer.states["card_reference"].visible is False


def test_a_line_typed_in_a_new_form_brings_back_the_totals():
    bus, repository = billing()

    answer = bus(
        QueryPreviewInvoice(
            values={
                "partner_id": "p1",
                "lines": [{"product": "x", "qty": 3, "price": "9.90"}],
            },
            changed=["lines"],
        ),
        ResponsePreviewInvoice,
    )

    assert answer.values == {"subtotal": "29.70", "total": "29.70"}
    assert repository.count(Invoice).value == 0


def test_a_wrong_cell_of_a_line_is_answered_pointing_at_that_line_and_that_cell():
    bus, _ = billing()
    lines = [
        {"product": "cuaderno", "qty": 10, "price": "12.50"},
        {"product": "lapiz", "qty": "dos", "price": "2"},
    ]

    with pytest.raises(ddd_exceptions.InvalidValue) as refused:
        bus(
            QueryPreviewInvoice(values={"partner_id": "p1", "lines": lines}),
            ResponsePreviewInvoice,
        )

    [error] = problem_of(refused.value).errors
    assert error.pointer == "#/values/lines/1/qty"
    assert "InvoiceLine.qty cannot take 'dos'" in error.detail


def test_a_line_with_a_field_it_does_not_have_points_at_that_line():
    invoice = Invoice(partner_id="p1")

    with pytest.raises(ddd_exceptions.InvalidValue) as refused:
        apply_values(
            invoice, {"lines": [{"product": "x", "qty": 1, "price": "1", "colour": "red"}]}
        )

    assert problem_of(refused.value).errors[0].pointer == "#/values/lines/0/colour"


def test_each_line_comes_back_with_how_a_form_shows_its_fields():
    bus, _ = billing()
    lines = [
        {"product": "cuaderno", "qty": 10, "price": "12.50"},
        {"product": "lapiz", "qty": 4, "price": "2"},
    ]

    answer = bus(
        QueryPreviewInvoice(values={"partner_id": "p1", "lines": lines}, changed=["lines"]),
        ResponsePreviewInvoice,
    )

    assert [sorted(line) for line in answer.states["lines"].items] == [
        ["price", "product", "qty"],
        ["price", "product", "qty"],
    ]
    assert answer.states["discount"].items == []


def test_editing_a_stored_invoice_answers_what_moved_and_leaves_the_stored_one_alone():
    stored = a_stored_invoice()
    bus, repository = billing(stored)

    answer = bus(
        QueryPreviewInvoice(id=stored.id, values={"discount": "50"}, changed=["discount"]),
        ResponsePreviewInvoice,
    )

    assert answer.values == {"total": "83.00"}
    assert answer.warnings[0].field == "discount"
    kept = repository.get(Invoice, stored.id)
    assert kept is not None
    assert (kept.discount, kept.total) == (Decimal("0"), Decimal("133.00"))


def test_paying_by_card_shows_and_asks_for_the_card_reference():
    stored = a_stored_invoice()
    bus, _ = billing(stored)

    answer = bus(
        QueryPreviewInvoice(id=stored.id, values={"payment": "card"}, changed=["payment"]),
        ResponsePreviewInvoice,
    )

    assert answer.values == {}
    assert answer.states["card_reference"].visible is True
    assert answer.states["card_reference"].required is True


def test_advice_travels_back_and_is_silent_outside_a_preview():
    invoice = a_stored_invoice()
    invoice.discount = Decimal("100")

    recompute(invoice, ("discount",))  # outside a preview: the warning goes nowhere
    with read_only() as warnings:
        recompute(invoice, ("discount",))

    assert [one.message for one in warnings] == ["a discount above 10% needs approval"]


def test_previewing_a_record_that_is_not_there_is_not_found():
    bus, _ = billing()

    with pytest.raises(ddd_exceptions.AggregateNotFound):
        bus(QueryPreviewInvoice(id="gone"), ResponsePreviewInvoice)


# ---------------------------------------------------------------------------------------------
# Nothing is written inside a preview
# ---------------------------------------------------------------------------------------------


@dataclass
class Folder(ArchivableMixin, Entity):
    name: str = ""


@pytest.mark.parametrize(
    "write",
    [
        lambda store, one: store.save(one),
        lambda store, one: store.remove(one),
        lambda store, one: store.archive(one),
        lambda store, one: store.upsert(Folder(name="x"), on=("name",)),
        lambda store, one: store.update_all(Folder, Criteria(), {"name": "y"}),
        lambda store, one: store.remove_all(Folder, Criteria()),
    ],
    ids=["save", "remove", "archive", "upsert", "update_all", "remove_all"],
)
def test_every_write_door_is_refused_inside_a_preview(write):
    folder = Folder(name="docs")
    store = MemoryRepository().add(folder)

    with read_only(), pytest.raises(exceptions.ProgrammingError):
        write(store, folder)

    assert [one.name for one in store.search(Folder)] == ["docs"]


@dataclass
class NumberedInvoice(Entity):
    number: str = "/"

    def number_of(self) -> str:
        return f"F-{numbering.next_number('invoice')}"

    computations = Computations["NumberedInvoice"](
        lambda i: (Compute(i.number, depends=(), by=NumberedInvoice.number_of),)
    )


class ResponsePreviewNumbered(ResponsePreviewEntity):
    pass


class QueryPreviewNumbered(PreviewEntity[NumberedInvoice, ResponsePreviewNumbered]):
    pass


def test_a_number_taken_by_domain_code_during_a_preview_is_refused_and_not_spent():
    bus = UseFramework("numbering-preview", log_after_execution=False)
    bus.add_dependency("repository", MemoryRepository())

    @bus.feature(QueryPreviewNumbered)
    class NumberedReads(EntityReads[NumberedInvoice]):
        pass

    with pytest.raises(exceptions.ProgrammingError, match="take inside a preview"):
        bus(QueryPreviewNumbered(), ResponsePreviewNumbered)

    assert numbering.next_number("invoice") == 1


def test_outside_a_preview_every_write_goes_through_as_before():
    store = MemoryRepository()
    with read_only():
        pass
    store.save(Folder(name="after"))

    assert store.count(Folder).value == 1


@dataclass
class CartLine:
    name: str
    qty: int = 1


@dataclass
class Cart(Entity):
    lines: list[CartLine] = field(default_factory=list)
    items: int = 0

    def items_of(self) -> int:
        for line in self.lines:  # a computation that should not, rewriting what it reads
            line.name = line.name.upper()
        return sum(line.qty for line in self.lines)

    computations = Computations["Cart"](
        lambda c: (Compute(c.items, depends=(c.lines,), by=Cart.items_of),)
    )


class ResponsePreviewCart(ResponsePreviewEntity):
    pass


class QueryPreviewCart(PreviewEntity[Cart, ResponsePreviewCart]):
    pass


def test_what_a_preview_does_to_a_line_never_reaches_the_stored_line():
    stored = Cart(lines=[CartLine("cuaderno", 2), CartLine("lapiz", 3)])
    bus = UseFramework("cart-preview", log_after_execution=False)
    repository = MemoryRepository().add(stored)
    bus.add_dependency("repository", repository)

    @bus.feature(QueryPreviewCart)
    class CartReads(EntityReads[Cart]):
        pass

    answer = bus(QueryPreviewCart(id=stored.id), ResponsePreviewCart)

    assert answer.values["items"] == 5
    kept = repository.get(Cart, stored.id)
    assert kept is not None
    assert [line.name for line in kept.lines] == ["cuaderno", "lapiz"]


# ---------------------------------------------------------------------------------------------
# What the review found: each case once broken, now held
# ---------------------------------------------------------------------------------------------


@dataclass
class TicketLine(Entity):
    qty: int = 1
    price: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")

    def amount_of(self) -> Decimal:
        return self.qty * self.price

    computations = Computations["TicketLine"](
        lambda t: (Compute(t.amount, depends=(t.qty, t.price), by=TicketLine.amount_of),)
    )


@dataclass
class Ticket(Entity):
    lines: list[TicketLine] = field(default_factory=list)
    total: Decimal = Decimal("0")

    def total_of(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0"))

    computations = Computations["Ticket"](
        lambda t: (Compute(t.total, depends=(t.lines,), by=Ticket.total_of),)
    )


def test_a_save_computes_the_lines_before_the_total_that_reads_them():
    repository = MemoryRepository()
    ticket = Ticket(lines=[TicketLine(qty=2, price=Decimal("10"))])

    repository.save(ticket)

    kept = repository.get(Ticket, ticket.id)
    assert kept is not None
    assert (kept.lines[0].amount, kept.total) == (Decimal("20"), Decimal("20"))


def test_assign_builds_the_lines_through_their_own_class_and_refuses_framework_fields():
    ticket = Ticket()

    apply_values(ticket, {"lines": [{"qty": 3, "price": "5"}]})

    assert isinstance(ticket.lines[0], TicketLine)
    assert (ticket.lines[0].qty, ticket.lines[0].price) == (3, Decimal("5"))
    with pytest.raises(exceptions.ClientError, match="version"):
        apply_values(ticket, {"version": 1})
    with pytest.raises(exceptions.ClientError, match="id"):
        apply_values(ticket, {"id": "another"})
    with pytest.raises(exceptions.ClientError, match="version"):
        apply_values(ticket, {"lines": [{"qty": 1, "version": 4}]})


def test_recompute_refuses_a_change_that_names_no_field():
    with pytest.raises(exceptions.ClientError, match="nonexistent"):
        recompute(Ticket(), ("nonexistent",))


def test_a_subclass_that_overrides_the_method_is_heard():
    @dataclass
    class Tariff(Entity):
        base: int = 1
        price: int = 0

        def price_of(self) -> int:
            return self.base

        computations = Computations["Tariff"](
            lambda t: (Compute(t.price, depends=(t.base,), by=Tariff.price_of),)
        )

    @dataclass
    class PeakTariff(Tariff):
        def price_of(self) -> int:
            return self.base * 100

    peak = PeakTariff(base=2)
    recompute(peak)

    assert peak.price == 200


def test_a_back_reference_in_the_copy_points_at_the_copy():
    @dataclass
    class Binder(Entity):
        memos: list["Memo"] = field(default_factory=list)

    @dataclass
    class Memo(Entity):
        binder: Binder | None = None

    binder = Binder()
    binder.memos.append(Memo(binder=binder))

    copy = copied(binder)

    assert copy is not binder
    assert copy.memos[0] is not binder.memos[0]
    assert copy.memos[0].binder is copy


def test_the_advice_of_a_preview_inside_another_reaches_the_outer_one():
    with read_only() as outer:
        with read_only() as inner:
            warn_form("revisar el descuento", field="discount")

    assert [one.message for one in inner] == ["revisar el descuento"]
    assert [one.message for one in outer] == ["revisar el descuento"]


def test_a_line_given_as_a_bare_value_is_refused_pointing_at_it():
    invoice = Invoice(partner_id="p1")

    with pytest.raises(
        ddd_exceptions.InvalidValue, match="holds InvoiceLine records"
    ) as refused:
        apply_values(invoice, {"lines": [{"product": "x", "qty": 1, "price": "1"}, "l9"]})

    assert problem_of(refused.value).errors[0].pointer == "#/values/lines/1"
    assert invoice.lines == []


def test_a_line_given_as_the_record_itself_is_held_as_it_is():
    invoice = Invoice(partner_id="p1")
    line = InvoiceLine("cuaderno", 2, Decimal("3"))

    apply_values(invoice, {"lines": [line]})

    assert invoice.lines == [line] and invoice.lines[0] is line


def test_a_rule_of_the_project_points_at_the_field_of_its_own_request():
    refused = ddd_exceptions.InvalidValue("not a valid RUC", ("tax_id",), field="customer")

    assert problem_of(refused).errors[0].pointer == "#/customer/tax_id"
    assert refused.within("billing").path == ("billing", "tax_id")


# ---------------------------------------------------------------------------------------------
# FormWarning about one line: where it is, as an error says it
# ---------------------------------------------------------------------------------------------


@dataclass
class SaleLine(Entity):
    product: str = ""
    qty: int = 1
    price: Decimal = Decimal("0")
    cost: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")

    def amount_of(self) -> Decimal:
        if self.price < self.cost:
            warn_form("price below cost", field="price")
        return self.qty * self.price

    computations = Computations["SaleLine"](
        lambda s: (Compute(s.amount, depends=(s.qty, s.price), by=SaleLine.amount_of),)
    )


@dataclass
class Sale(Entity):
    lines: list[SaleLine] = field(default_factory=list)
    total: Decimal = Decimal("0")

    def total_of(self) -> Decimal:
        if len(self.lines) > 2:
            warn_form("more than two lines need a review")
        return sum((line.amount for line in self.lines), Decimal("0"))

    computations = Computations["Sale"](
        lambda s: (Compute(s.total, depends=(s.lines,), by=Sale.total_of),)
    )


class ResponsePreviewSale(ResponsePreviewEntity):
    pass


class QueryPreviewSale(PreviewEntity[Sale, ResponsePreviewSale]):
    pass


def test_the_advice_a_line_gives_points_at_that_line_and_that_cell():
    sale = Sale(
        lines=[
            SaleLine("cuaderno", 1, Decimal("10"), Decimal("4")),
            SaleLine("lapiz", 2, Decimal("1"), Decimal("3")),
        ]
    )

    with read_only() as warnings:
        recompute(sale)

    assert [(one.message, one.field, one.path) for one in warnings] == [
        ("price below cost", "lines", ("lines", 1, "price")),
    ]


def test_the_advice_about_the_record_and_about_a_field_say_where_they_are():
    sale = Sale(lines=[SaleLine("a"), SaleLine("b"), SaleLine("c")])
    invoice = a_stored_invoice()
    invoice.discount = Decimal("100")

    with read_only() as warnings:
        recompute(sale)
        recompute(invoice, ("discount",))

    assert [(one.field, one.path) for one in warnings] == [
        (None, ()),
        ("discount", ("discount",)),
    ]


def test_a_preview_answers_the_advice_of_a_line_typed_in_a_new_form():
    bus = UseFramework("sale-preview", log_after_execution=False)
    bus.add_dependency("repository", MemoryRepository())

    @bus.feature(QueryPreviewSale)
    class SaleReads(EntityReads[Sale]):
        pass

    answer = bus(
        QueryPreviewSale(
            values={
                "lines": [
                    {"product": "cuaderno", "qty": 1, "price": "10", "cost": "4"},
                    {"product": "lapiz", "qty": 2, "price": "1", "cost": "3"},
                ]
            },
            changed=["lines"],
        ),
        ResponsePreviewSale,
    )

    assert answer.values["total"] == "12"
    assert answer.model_dump(mode="json")["warnings"] == [
        {"message": "price below cost", "field": "lines", "path": ["lines", 1, "price"]},
    ]


def test_advice_given_inside_nested_blocks_adds_their_places_up():
    with read_only() as warnings:
        with warnings_at(("lines", 0)):
            with warnings_at(("components", 3)):
                warn_form("out of stock", field="qty")

    assert warnings[0].path == ("lines", 0, "components", 3, "qty")
    assert warnings[0].field == "lines"
