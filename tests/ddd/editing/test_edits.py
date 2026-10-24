"""The life of a form, one command: `Edit` keeps the caller's draft of a record, saves it or
discards it; `Get(draft=True)` and `Search(drafts=True)` find it again after the client lost it.

The cases are an invoicing screen used by two cashiers of one shop and one of another: lines of
quantity × price make a subtotal, a discount makes the total. Every case runs with the context
kept in memory and over a key-value store — what production keeps in Redis.
"""

from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from sincpro_framework import UseFramework, exceptions
from sincpro_framework.context import ContextStore, InMemoryContexts, KeyValueContexts
from sincpro_framework.context.domain import exceptions as context_exceptions
from sincpro_framework.data_layer.repositories import MemoryNumbering, MemoryRepository
from sincpro_framework.ddd import (
    Criteria,
    Derivations,
    Derive,
    DraftState,
    Edit,
    EditAction,
    EntityEdits,
    Get,
    ResponseEdit,
    ResponsePaginatedQuery,
    ResponseRecord,
    Search,
    Specification,
)
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd.criteria import parse_order
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.store import InMemoryKeyValue


@dataclass
class InvoiceLine:
    product: str
    qty: int
    price: Decimal


@dataclass(kw_only=True)
class LineAdded(DomainEvent):
    name = "billing.invoice.v1.line_added"
    product: str = ""


@dataclass
class Invoice(Entity):
    partner_id: str
    lines: list[InvoiceLine] = field(default_factory=list)
    discount: Decimal = Decimal("0")
    subtotal: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    state: str = "draft"
    number: str = "/"

    def post(self, number: int) -> None:
        """Posting fixes the invoice: it takes its number and leaves the draft state."""
        if self.state == "posted":
            raise ddd_exceptions.DomainError(f"invoice {self.number} is already posted")
        self.state, self.number = "posted", f"F-{number:04d}"

    def add_line(self, product: str, qty: int, price: Decimal) -> None:
        self.lines = [*self.lines, InvoiceLine(product, qty, price)]
        self.record(LineAdded(entity_id=self.id, product=product))

    def subtotal_of(self) -> Decimal:
        return sum((line.qty * line.price for line in self.lines), Decimal("0"))

    def total_of(self) -> Decimal:
        return self.subtotal - self.discount

    derivations = Derivations["Invoice"](
        lambda i: (
            Derive(i.total, depends=(i.subtotal, i.discount), by=Invoice.total_of),
            Derive(i.subtotal, depends=(i.lines,), by=Invoice.subtotal_of),
        )
    )


class ResponseInvoice(ResponseRecord):
    invoice: Invoice


class ResponseEditInvoice(ResponseEdit):
    invoice: Invoice


class ResponseInvoices(ResponsePaginatedQuery):
    invoices: list[Invoice]


class QueryGetInvoice(Get[Invoice, ResponseInvoice]):
    pass


class QueryListInvoices(Search[Invoice, ResponseInvoices]):
    pass


class CommandEditInvoice(Edit[Invoice, ResponseEditInvoice]):
    pass


class CommandPostInvoice(Edit[Invoice, ResponseEditInvoice]):
    """An ERP's confirm step: a transition is an edit that saves — the draft, if any, then the
    domain's own method, stored in one write."""

    action: EditAction = EditAction.SAVE


class CommandAddInvoiceLine(Edit[Invoice, ResponseEditInvoice]):
    product: str
    qty: int
    price: Decimal


ANA = {"tenant_id": "shop-1", "user_id": "ana"}
BOB = {"tenant_id": "shop-1", "user_id": "bob"}
ANA_ELSEWHERE = {"tenant_id": "shop-2", "user_id": "ana"}
LINES = [{"product": "cuaderno", "qty": 10, "price": "12.50"}]


def drafted(answer: ResponseRecord) -> DraftState:
    assert answer.draft is not None
    return answer.draft


@pytest.fixture(params=["memory", "key_value"])
def context_store(request) -> ContextStore:
    if request.param == "memory":
        return InMemoryContexts()
    return KeyValueContexts(InMemoryKeyValue())


@dataclass
class Shop:
    bus: UseFramework
    repository: MemoryRepository

    def edit(self, who: dict, **asked) -> ResponseEditInvoice:
        with self.bus.context(who):
            return self.bus(CommandEditInvoice(**asked), ResponseEditInvoice)

    def post(self, who: dict, **asked) -> ResponseEditInvoice:
        with self.bus.context(who):
            return self.bus(CommandPostInvoice(**asked), ResponseEditInvoice)

    def add_line(self, who: dict, **asked) -> ResponseEditInvoice:
        with self.bus.context(who):
            return self.bus(CommandAddInvoiceLine(**asked), ResponseEditInvoice)

    def get(self, who: dict, **asked) -> ResponseInvoice:
        with self.bus.context(who):
            return self.bus(QueryGetInvoice(**asked), ResponseInvoice)

    def stored(self, key: str) -> Invoice:
        found = self.repository.get(Invoice, key)
        assert found is not None
        return found

    def search(self, who: dict, criteria: Criteria) -> ResponseInvoices:
        with self.bus.context(who):
            return self.bus(QueryListInvoices(criteria=criteria), ResponseInvoices)

    def drafts(self, who: dict, criteria: Criteria = Criteria()) -> ResponseInvoices:
        with self.bus.context(who):
            return self.bus(
                QueryListInvoices(criteria=criteria, drafts=True), ResponseInvoices
            )


@pytest.fixture
def shop(context_store: ContextStore) -> Shop:
    bus = UseFramework("edits-shop", log_after_execution=False)
    repository = MemoryRepository()
    bus.add_dependency("repository", repository)
    bus.store_context_in(context_store)

    @bus.feature([QueryGetInvoice, QueryListInvoices, CommandEditInvoice])
    class InvoiceForms(EntityEdits[Invoice]):
        pass

    numbering = MemoryNumbering()

    @bus.feature(CommandPostInvoice)
    class PostInvoice(EntityEdits[Invoice]):
        def change(self, invoice: Invoice, dto: CommandPostInvoice, /) -> None:
            super().change(invoice, dto)
            invoice.post(numbering.take("invoices", scope=invoice.partner_id)[0])

    @bus.feature(CommandAddInvoiceLine)
    class AddInvoiceLine(EntityEdits[Invoice]):
        def change(self, invoice: Invoice, dto: CommandAddInvoiceLine) -> None:
            invoice.add_line(dto.product, dto.qty, dto.price)

    return Shop(bus, repository)


def a_stored_invoice(shop: Shop) -> Invoice:
    invoice = Invoice(partner_id="p1", lines=[InvoiceLine("lapiz", 4, Decimal("2"))])
    invoice.subtotal = invoice.total = Decimal("8")
    shop.repository.save(invoice)
    return invoice


# ---------------------------------------------------------------------------------------------
# Creating: nothing is stored until it is saved
# ---------------------------------------------------------------------------------------------


def test_a_new_record_is_kept_as_a_draft_with_its_derived_fields_and_nothing_is_stored(
    shop: Shop,
):
    answered = shop.edit(ANA, values={"partner_id": "p1", "lines": LINES})

    assert answered.draft is not None and drafted(answered).version > 0
    assert answered.invoice.total == Decimal("125.00")
    assert answered.fields["total"].visible
    assert shop.repository.get(Invoice, answered.invoice.id) is None


def test_the_id_of_a_new_record_is_the_one_the_client_named_or_one_minted_for_it(shop: Shop):
    named = shop.edit(ANA, id="0192aaaa", values={"partner_id": "p1"})
    minted = shop.edit(ANA, values={"partner_id": "p2"})

    assert named.invoice.id == "0192aaaa"
    assert len(minted.invoice.id) == 32


def test_saving_a_drafted_record_stores_it_and_drops_the_draft(shop: Shop):
    kept = shop.edit(ANA, id="0192aaaa", values={"partner_id": "p1", "lines": LINES})
    assert kept.draft is not None

    saved = shop.edit(
        ANA, id="0192aaaa", version=drafted(kept).version, action=EditAction.SAVE
    )

    stored = shop.repository.get(Invoice, "0192aaaa")
    assert stored is not None and stored.total == Decimal("125.00")
    assert saved.draft is None and saved.invoice.version == stored.version
    assert shop.get(ANA, id="0192aaaa", draft=True).draft is None


def test_a_save_with_no_draft_stores_the_values_at_once(shop: Shop):
    saved = shop.edit(ANA, id="0192bbbb", values={"partner_id": "p9"}, action=EditAction.SAVE)

    assert saved.draft is None
    assert shop.stored("0192bbbb").partner_id == "p9"


# ---------------------------------------------------------------------------------------------
# Updating: the stored record stays as it was until the draft is saved
# ---------------------------------------------------------------------------------------------


def test_editing_a_stored_record_drafts_over_it_and_leaves_it_alone(shop: Shop):
    invoice = a_stored_invoice(shop)

    answered = shop.edit(ANA, id=invoice.id, values={"partner_id": "p1", "discount": "3"})

    assert answered.invoice.total == Decimal("5")
    assert answered.draft is not None and drafted(answered).origin_version == invoice.version
    assert shop.stored(invoice.id).discount == Decimal("0")


def test_a_command_of_the_projects_own_edits_with_the_domains_methods(shop: Shop):
    invoice = a_stored_invoice(shop)

    kept = shop.add_line(ANA, id=invoice.id, product="regla", qty=2, price=Decimal("5"))
    assert kept.invoice.total == Decimal("18") and kept.draft is not None
    assert len(shop.stored(invoice.id).lines) == 1

    shop.add_line(
        ANA,
        id=invoice.id,
        product="goma",
        qty=1,
        price=Decimal("1"),
        version=drafted(kept).version,
        action=EditAction.SAVE,
    )
    stored = shop.stored(invoice.id)
    assert [line.product for line in stored.lines] == ["lapiz", "regla", "goma"]
    assert stored.total == Decimal("19")


def test_a_draft_over_a_record_someone_saved_meanwhile_is_stale_and_its_save_refused(
    shop: Shop,
):
    invoice = a_stored_invoice(shop)
    kept = shop.edit(ANA, id=invoice.id, values={"partner_id": "p1", "discount": "1"})

    shop.edit(BOB, id=invoice.id, values={"partner_id": "p7"}, action=EditAction.SAVE)

    assert drafted(shop.get(ANA, id=invoice.id, draft=True)).stale
    with pytest.raises(ddd_exceptions.StaleAggregate, match="changed since the edit started"):
        shop.edit(ANA, id=invoice.id, version=drafted(kept).version, action=EditAction.SAVE)


def test_discarding_drops_the_draft_and_answers_the_record_as_stored(shop: Shop):
    invoice = a_stored_invoice(shop)
    shop.edit(ANA, id=invoice.id, values={"partner_id": "p1", "discount": "2"})

    answered = shop.edit(ANA, id=invoice.id, action=EditAction.DISCARD)

    assert answered.draft is None and answered.invoice.discount == Decimal("0")
    assert shop.get(ANA, id=invoice.id, draft=True).draft is None


# ---------------------------------------------------------------------------------------------
# Whose: two cashiers, two shops, two tabs
# ---------------------------------------------------------------------------------------------


def test_two_cashiers_editing_one_invoice_each_keep_their_own_draft(shop: Shop):
    invoice = a_stored_invoice(shop)
    shop.edit(ANA, id=invoice.id, values={"partner_id": "p1", "discount": "1"})
    shop.edit(BOB, id=invoice.id, values={"partner_id": "p1", "discount": "2"})

    assert shop.get(ANA, id=invoice.id, draft=True).invoice.discount == Decimal("1")
    assert shop.get(BOB, id=invoice.id, draft=True).invoice.discount == Decimal("2")


def test_a_form_whose_drafts_belong_to_nobody_shares_one_draft_of_a_record(
    context_store: ContextStore,
):
    """`drafts_by = ()`: every user who opens the record edits the same draft — the last
    save names the version the previous one left."""
    bus = UseFramework("edits-shared", log_after_execution=False)
    bus.add_dependency("repository", MemoryRepository())
    bus.store_context_in(context_store)

    @bus.feature([QueryGetInvoice, QueryListInvoices, CommandEditInvoice])
    class SharedForms(EntityEdits[Invoice]):
        drafts_by = ()

    shared = Shop(bus, MemoryRepository())
    first = shared.edit(ANA, id="0192cccc", values={"partner_id": "p1"})
    second = shared.edit(
        ANA_ELSEWHERE,
        id="0192cccc",
        version=drafted(first).version,
        values={"partner_id": "p2"},
    )

    seen_by_bob = shared.get(BOB, id="0192cccc", draft=True)
    assert seen_by_bob.draft is not None and seen_by_bob.invoice.partner_id == "p2"
    assert drafted(second).version == seen_by_bob.draft.version


def test_a_draft_never_crosses_shops_even_for_the_same_cashier(shop: Shop):
    shop.edit(ANA, id="0192cccc", values={"partner_id": "p1"})

    assert shop.drafts(ANA_ELSEWHERE).invoices == []


def test_the_second_tab_that_kept_on_an_old_version_loses_and_reads_again(shop: Shop):
    first = shop.edit(ANA, id="0192dddd", values={"partner_id": "p1"})
    assert first.draft is not None
    shop.edit(ANA, id="0192dddd", values={"partner_id": "p2"}, version=drafted(first).version)

    with pytest.raises(context_exceptions.DraftConflict):
        shop.edit(
            ANA, id="0192dddd", values={"partner_id": "p3"}, version=drafted(first).version
        )
    with pytest.raises(context_exceptions.DraftConflict):
        shop.edit(ANA, id="0192dddd", version=drafted(first).version, action=EditAction.SAVE)


def test_an_edit_with_nobody_in_the_context_is_kept_for_the_tenant(shop: Shop):
    kept = shop.edit({"tenant_id": "shop-1"}, id="0192eeee", values={"partner_id": "p1"})

    assert kept.draft is not None


# ---------------------------------------------------------------------------------------------
# The client lost its state: the browser was closed, the agent's conversation ended
# ---------------------------------------------------------------------------------------------


def test_after_the_browser_closed_the_record_is_read_where_the_cashier_left_it(shop: Shop):
    invoice = a_stored_invoice(shop)
    kept = shop.edit(ANA, id=invoice.id, values={"partner_id": "p1", "discount": "3"})

    again = shop.get(ANA, id=invoice.id, draft=True)

    assert again.invoice.discount == Decimal("3") and again.invoice.total == Decimal("5")
    assert again.draft is not None and drafted(again).version == drafted(kept).version
    assert shop.get(ANA, id=invoice.id).draft is None
    assert shop.get(ANA, id=invoice.id).invoice.discount == Decimal("0")


def test_a_record_only_drafted_so_far_is_found_by_its_own_id(shop: Shop):
    shop.edit(ANA, id="0192eeee", values={"partner_id": "p1", "lines": LINES})

    again = shop.get(ANA, id="0192eeee", draft=True)

    assert again.invoice.partner_id == "p1" and again.draft is not None


def test_what_was_i_editing_is_a_search_over_the_cashiers_drafts(shop: Shop):
    invoice = a_stored_invoice(shop)
    shop.edit(ANA, id="0192ffff", values={"partner_id": "p1", "lines": LINES})
    shop.edit(ANA, id=invoice.id, values={"partner_id": "p2"})
    shop.edit(BOB, id="0192gggg", values={"partner_id": "p3"})

    mine = shop.drafts(ANA).invoices
    by_partner = shop.drafts(ANA, Criteria(order=parse_order("partner_id"))).invoices
    big = shop.drafts(
        ANA,
        Criteria.model_validate({"where": {"field": "total", "operator": ">", "value": 100}}),
    ).invoices

    assert [one.id for one in mine] == [invoice.id, "0192ffff"]
    assert [one.partner_id for one in by_partner] == ["p1", "p2"]
    assert [one.id for one in big] == ["0192ffff"]


def test_two_bounded_contexts_sharing_one_store_never_read_each_others_drafts(
    context_store: ContextStore,
):
    """Billing and purchases keep their drafts in one Redis, each with an `Invoice` of its own and
    the same cashier: each reads only its own."""
    shops = []
    for name in ("billing", "purchases"):
        bus = UseFramework(name, log_after_execution=False)
        bus.add_dependency("repository", MemoryRepository())
        bus.store_context_in(context_store)

        @bus.feature([QueryGetInvoice, QueryListInvoices, CommandEditInvoice])
        class InvoiceForms(EntityEdits[Invoice]):
            pass

        shops.append(Shop(bus, MemoryRepository()))
    billing, purchases = shops

    billing.edit(ANA, id="0192hhhh", values={"partner_id": "customer"})
    purchases.edit(ANA, id="0192hhhh", values={"partner_id": "supplier"})

    assert billing.get(ANA, id="0192hhhh", draft=True).invoice.partner_id == "customer"
    assert purchases.get(ANA, id="0192hhhh", draft=True).invoice.partner_id == "supplier"
    assert len(billing.drafts(ANA).invoices) == 1


def test_a_value_the_form_cannot_send_is_the_callers_to_fix(shop: Shop):
    """A field the record does not have, a value its type cannot read, a field the framework
    writes: the request is wrong — a `ClientError`, answered as one by every wire."""
    for values in (
        {"partner_id": "p1", "colour": "red"},
        {"partner_id": "p1", "discount": "a lot"},
        {"partner_id": "p1", "version": 7},
    ):
        with pytest.raises(exceptions.ClientError):
            shop.edit(ANA, values=values)


def test_posting_a_drafted_invoice_saves_the_draft_and_numbers_it_in_one_write(shop: Shop):
    """The ERP's confirm step on top of a form: the cashier's draft, then `post`, stored at once;
    the number is taken only when the invoice is posted, never by a draft."""
    kept = shop.edit(ANA, id="0192iiii", values={"partner_id": "p1", "lines": LINES})
    assert shop.repository.get(Invoice, "0192iiii") is None

    posted = shop.post(ANA, id="0192iiii", version=drafted(kept).version)

    stored = shop.stored("0192iiii")
    assert (stored.state, stored.number, stored.total) == (
        "posted",
        "F-0001",
        Decimal("125.00"),
    )
    assert posted.draft is None and posted.invoice.number == "F-0001"
    with pytest.raises(ddd_exceptions.DomainError, match="already posted"):
        shop.post(ANA, id="0192iiii")


PARTNER_ONLY = Criteria(specification=Specification.model_validate({"partner_id": {}}))


def test_a_draft_goes_out_as_the_stored_record_does_for_the_same_criteria(shop: Shop):
    """One question, one shape: the fields the criteria names and the model's definition, whether
    the answer is the stored record or the caller's draft — only `draft` tells them apart."""
    invoice = a_stored_invoice(shop)
    shop.edit(ANA, id=invoice.id, values={"partner_id": "p2"})

    stored = shop.get(ANA, id=invoice.id, criteria=PARTNER_ONLY).model_dump(mode="json")
    drafted_one = shop.get(ANA, id=invoice.id, criteria=PARTNER_ONLY, draft=True).model_dump(
        mode="json"
    )
    listed = shop.search(ANA, PARTNER_ONLY).model_dump(mode="json")
    listed_drafts = shop.drafts(ANA, PARTNER_ONLY).model_dump(mode="json")

    assert sorted(drafted_one["invoice"]) == sorted(stored["invoice"]) == ["id", "partner_id"]
    assert drafted_one["entity_meta_data"] == stored["entity_meta_data"] is not None
    assert (stored["invoice"]["partner_id"], drafted_one["invoice"]["partner_id"]) == (
        "p1",
        "p2",
    )
    assert sorted(listed_drafts["invoices"][0]) == sorted(listed["invoices"][0])
    assert listed_drafts["entity_meta_data"] == listed["entity_meta_data"] is not None
    assert (stored["draft"], drafted_one["draft"]["version"] > 0) == (None, True)


def test_an_edit_answers_the_record_whole_with_the_models_definition(shop: Shop):
    answered = shop.edit(ANA, values={"partner_id": "p1", "lines": LINES}).model_dump(
        mode="json"
    )

    assert answered["invoice"]["lines"][0]["product"] == "cuaderno"
    assert answered["entity_meta_data"]["fields"]["total"] is not None
