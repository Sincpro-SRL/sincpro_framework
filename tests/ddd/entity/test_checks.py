"""The rules a sale order holds over its own fields, and the values it proposes while it is typed.

The cases are a sales screen: a confirmed order needs a delivery date, a line sells at least one
unit, a product appears once, and picking a customer or a product proposes a payment term or a
price the user may type over. Each rule is written once, on the entity; where it runs decides
whether it warns the form or refuses the save.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

import pytest
from structlog.testing import capture_logs

from sincpro_framework import UseFramework
from sincpro_framework.common.failures import problem_of
from sincpro_framework.context import ContextKey, InMemoryContexts
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    Edit,
    EditAction,
    Entity,
    EntityEdits,
    EntityReads,
    PreviewEntity,
    ResponseEdit,
    ResponsePreviewEntity,
    check,
    compute,
)
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd import presentation_of, read_only, recompute
from sincpro_framework.ddd.entity.entity_meta import checks_of

PRICES = {"desk": Decimal("100"), "chair": Decimal("40")}
TERMS = {"acme": "30 days", "walk-in": "cash"}


class SaleState(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


@dataclass
class SaleLine:
    product: str = ""
    qty: int = 1
    price: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")

    @compute(lambda line: line.price, depends=lambda line: line.product, editable=True)
    def price_of(self) -> Decimal:
        return PRICES.get(self.product, Decimal("0"))

    @compute(lambda line: line.amount, depends=lambda line: [line.qty, line.price])
    def amount_of(self) -> Decimal:
        return self.qty * self.price

    @check(lambda line: line.qty)
    def sells_something(self) -> None:
        if self.qty < 1:
            raise ddd_exceptions.InvalidValue("A line sells at least one unit")


@dataclass
class Sale(Entity):
    customer: str = ""
    payment_term: str = ""
    state: SaleState = SaleState.DRAFT
    delivery: date | None = None
    lines: list[SaleLine] = field(default_factory=list)
    total: Decimal = Decimal("0")

    @compute(lambda s: s.payment_term, depends=lambda s: s.customer, editable=True)
    def payment_term_of(self) -> str:
        return TERMS.get(self.customer, "")

    @compute(lambda s: s.total, depends=lambda s: s.lines)
    def total_of(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0"))

    @check(lambda s: [s.delivery, s.state])
    def delivery_when_confirmed(self) -> None:
        if self.state == SaleState.CONFIRMED and self.delivery is None:
            raise ddd_exceptions.InvalidValue("A confirmed sale needs a delivery date")

    @check(lambda s: s.lines)
    def each_product_once(self) -> None:
        seen: set[str] = set()
        for position, line in enumerate(self.lines):
            if line.product in seen:
                raise ddd_exceptions.InvalidValue(
                    f"{line.product} is already on the sale", ("lines", position, "product")
                )
            seen.add(line.product)


def confirmed_without_delivery() -> Sale:
    sale = Sale(
        customer="acme",
        state=SaleState.CONFIRMED,
        lines=[SaleLine("desk", 1, Decimal("100")), SaleLine("desk", 0, Decimal("100"))],
    )
    recompute(sale)
    return sale


# ---------------------------------------------------------------------------------------------
# a save refuses
# ---------------------------------------------------------------------------------------------


def test_a_save_refuses_every_broken_rule_at_once_each_where_it_points():
    repository = MemoryRepository()

    with pytest.raises(ddd_exceptions.ChecksFailed) as refused:
        repository.save(confirmed_without_delivery())

    paths = [one.path for one in refused.value.failures]
    assert paths == [("lines", 1, "qty"), ("delivery",), ("lines", 1, "product")]
    assert not repository.search(Sale).items


def test_a_failure_with_no_path_points_at_the_first_field_the_check_watches():
    with pytest.raises(ddd_exceptions.ChecksFailed) as refused:
        MemoryRepository().save(Sale(state=SaleState.CONFIRMED))

    assert [one.path for one in refused.value.failures] == [("delivery",)]
    assert "needs a delivery date" in str(refused.value)


def test_the_refusal_answers_one_field_error_per_failure():
    with pytest.raises(ddd_exceptions.ChecksFailed) as refused:
        MemoryRepository().save(confirmed_without_delivery())

    problem = problem_of(refused.value)

    assert problem.status == 422
    assert [one.pointer for one in problem.errors] == [
        "#/values/lines/1/qty",
        "#/values/delivery",
        "#/values/lines/1/product",
    ]


def test_a_sale_that_holds_its_rules_is_saved_with_its_computations():
    repository = MemoryRepository()
    sale = Sale(
        customer="acme",
        state=SaleState.CONFIRMED,
        delivery=date(2026, 11, 30),
        lines=[SaleLine("desk", 2, Decimal("90"))],
    )

    repository.save(sale)

    assert sale.total == Decimal("180")
    assert repository.get(Sale, sale.id) is not None


def test_a_checks_failure_is_an_invalid_value_for_whoever_handles_one():
    with pytest.raises(ddd_exceptions.InvalidValue):
        MemoryRepository().save(Sale(state=SaleState.CONFIRMED))


# ---------------------------------------------------------------------------------------------
# a change in a form warns
# ---------------------------------------------------------------------------------------------


def test_a_change_warns_of_the_checks_it_reaches_and_refuses_nothing():
    sale = confirmed_without_delivery()

    with read_only() as warnings:
        recompute(sale, ["state"])

    assert [(one.message, one.path) for one in warnings] == [
        ("A confirmed sale needs a delivery date", ("delivery",))
    ]


def test_a_change_to_one_cell_warns_of_that_line_only():
    sale = Sale(lines=[SaleLine("desk", 0), SaleLine("chair", 0)])

    with read_only() as warnings:
        recompute(sale, ["lines.1.qty"])

    assert [one.path for one in warnings] == [("lines", 1, "qty")]


def test_a_list_changed_whole_checks_every_line_and_what_watches_the_list():
    sale = Sale(lines=[SaleLine("desk", 0), SaleLine("desk", 1)])

    with read_only() as warnings:
        recompute(sale, ["lines"])

    assert [one.path for one in warnings] == [("lines", 0, "qty"), ("lines", 1, "product")]


def test_nothing_changed_yet_warns_of_nothing():
    with read_only() as warnings:
        recompute(confirmed_without_delivery())

    assert warnings == []


def test_outside_a_preview_a_change_checks_nothing_the_save_checks_them_all():
    sale = confirmed_without_delivery()

    recompute(sale, ["state", "lines"])

    with pytest.raises(ddd_exceptions.ChecksFailed):
        MemoryRepository().save(sale)


def test_a_path_that_names_no_position_is_refused():
    with pytest.raises(ddd_exceptions.ClientError, match="lines.2.qty"):
        recompute(Sale(lines=[SaleLine()]), ["lines.qty"])


class ResponsePreviewSale(ResponsePreviewEntity):
    pass


class QueryPreviewSale(PreviewEntity[Sale, ResponsePreviewSale]):
    pass


def test_a_preview_answers_the_warnings_of_the_checks_its_change_reached():
    bus = UseFramework("sales-checks", log_after_execution=False)
    bus.add_dependency("repository", MemoryRepository())

    @bus.feature(QueryPreviewSale)
    class SaleReads(EntityReads[Sale]):
        pass

    answered = bus(
        QueryPreviewSale(values={"state": "confirmed"}, changed=["state"]),
        ResponsePreviewSale,
    )

    assert answered is not None
    assert [one.path for one in answered.warnings] == [("delivery",)]


# ---------------------------------------------------------------------------------------------
# editable computations propose
# ---------------------------------------------------------------------------------------------


def test_an_editable_computation_proposes_a_value_when_a_change_reaches_it():
    sale = Sale(customer="acme")

    recompute(sale, ["customer"])

    assert sale.payment_term == "30 days"


def test_a_value_typed_over_a_proposal_is_the_one_a_save_stores():
    repository = MemoryRepository()
    sale = Sale(customer="acme")
    recompute(sale, ["customer"])
    sale.payment_term = "60 days"

    repository.save(sale)

    stored = repository.get(Sale, sale.id)
    assert stored is not None and stored.payment_term == "60 days"


def test_a_line_proposes_its_price_when_its_product_is_picked():
    sale = Sale(lines=[SaleLine("desk", 2), SaleLine("chair", 3)])

    recompute(sale, ["lines.1.product"])

    assert [line.price for line in sale.lines] == [Decimal("0"), Decimal("40")]
    assert sale.total == Decimal("120")


def test_nothing_changed_yet_proposes_nothing():
    sale = Sale(customer="acme")

    recompute(sale)

    assert sale.payment_term == ""


def test_an_editable_field_is_not_published_read_only_and_a_computed_one_is():
    readonly = presentation_of(Sale).readonly

    assert "total" in readonly
    assert "payment_term" not in readonly


# ---------------------------------------------------------------------------------------------
# declared once, read when the class is described
# ---------------------------------------------------------------------------------------------


def test_a_check_that_names_no_field_is_refused_when_the_class_is_read():
    @dataclass
    class Broken(Entity):
        a: int = 0

        @check(lambda b: b.nothing)
        def rule(self) -> None:
            pass

    with pytest.raises(ddd_exceptions.ProgrammingError, match="rule's @check names nothing"):
        checks_of(Broken)


def test_a_subclass_overrides_how_a_rule_is_checked_and_keeps_what_it_watches():
    @dataclass
    class LenientSale(Sale):
        def delivery_when_confirmed(self) -> None:
            pass

    watched = {one.method: one.watches for one in checks_of(LenientSale)}

    assert watched["delivery_when_confirmed"] == ("delivery", "state")
    MemoryRepository().save(LenientSale(state=SaleState.CONFIRMED))


# ---------------------------------------------------------------------------------------------
# an import skips the rules, out loud
# ---------------------------------------------------------------------------------------------


def test_a_call_that_skips_the_checks_saves_what_breaks_them_and_says_why_once():
    bus = UseFramework("sales-import", log_after_execution=False)
    repository = MemoryRepository()

    with capture_logs() as logs, bus.context(
        {
            ContextKey.SKIP_CHECKS: "import of the old sales, batch 7",
            ContextKey.USER_ID: "ana",
        }
    ):
        for _ in range(3):
            repository.save(confirmed_without_delivery())

    said = [one["event"] for one in logs if "without its checks" in one["event"]]
    assert len(repository.search(Sale).items) == 3
    assert len(said) == 1
    assert "batch 7" in said[0] and "ana" in said[0]


def test_skipping_the_checks_still_computes():
    bus = UseFramework("sales-import-totals", log_after_execution=False)
    sale = Sale(state=SaleState.CONFIRMED, lines=[SaleLine("desk", 2, Decimal("10"))])

    with bus.context({ContextKey.SKIP_CHECKS: "migration"}):
        MemoryRepository().save(sale)

    assert sale.total == Decimal("20")


# ---------------------------------------------------------------------------------------------
# a form's whole life: the draft warns, the save refuses
# ---------------------------------------------------------------------------------------------


class ResponseEditSale(ResponseEdit):
    sale: Sale


class CommandEditSale(Edit[Sale, ResponseEditSale]):
    pass


def test_a_draft_is_warned_and_saved_and_its_save_is_refused_until_the_rule_holds():
    bus = UseFramework("sales-edits", log_after_execution=False)
    repository = MemoryRepository()
    bus.add_dependency("repository", repository)
    bus.store_context_in(InMemoryContexts())

    @bus.feature(CommandEditSale)
    class SaleForms(EntityEdits[Sale]):
        pass

    who = {ContextKey.USER_ID: "ana"}
    with bus.context(who):
        drafted = bus(
            CommandEditSale(
                id="S-1",
                values={"customer": "acme", "state": "confirmed"},
                changed=["customer", "state"],
                action=EditAction.SAVE_DRAFT,
            ),
            ResponseEditSale,
        )
    assert drafted is not None and drafted.draft is not None
    assert [one.path for one in drafted.warnings] == [("delivery",)]
    assert drafted.sale.payment_term == "30 days"

    with pytest.raises(ddd_exceptions.ChecksFailed), bus.context(who):
        bus(
            CommandEditSale(id="S-1", version=drafted.draft.version, action=EditAction.SAVE),
            ResponseEditSale,
        )

    with bus.context(who):
        saved = bus(
            CommandEditSale(
                id="S-1",
                values={"delivery": "2026-11-30"},
                changed=["delivery"],
                version=drafted.draft.version,
                action=EditAction.SAVE,
            ),
            ResponseEditSale,
        )
    assert saved is not None and saved.sale.delivery == date(2026, 11, 30)
    assert repository.get(Sale, "S-1") is not None
