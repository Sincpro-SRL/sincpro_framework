"""The preview over a database: a sale with its lines in their own table, read with its
declared detail, previewed with other lines, and never written while it is.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Numeric, Text, event, text
from sqlalchemy.orm import registry
from structlog.testing import capture_logs

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.orm import map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.domain.transaction import Writes
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.numbering import DatabaseNumbering
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.numbering import (
    numbering_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import (
    Database,
    _reads_only,
)
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    Condition,
    ContractViolation,
    Criteria,
    Derivations,
    Derive,
    EntityReads,
    InMemoryDrafts,
    Preview,
    ResponsePreview,
    Specification,
    StaleAggregate,
    WriteInPreview,
    assign,
    previewing,
    refuse_stale,
)
from sincpro_framework.ddd.entity import Entity


@dataclass
class SaleLine(Entity):
    sale_id: str
    product: str = ""
    qty: int = 1
    price: Decimal = Decimal("0")


@dataclass
class Sale(Entity):
    customer_id: str
    lines: list[SaleLine] = field(default_factory=list)
    discount: Decimal = Decimal("0")
    subtotal: Decimal = Decimal("0")
    total: Decimal = Decimal("0")

    def subtotal_of(self) -> Decimal:
        return sum((line.qty * line.price for line in self.lines), Decimal("0"))

    def total_of(self) -> Decimal:
        return self.subtotal - self.discount

    derivations = Derivations["Sale"](
        lambda s: (
            Derive(s.subtotal, depends=(s.lines,), by=Sale.subtotal_of),
            Derive(s.total, depends=(s.subtotal, s.discount), by=Sale.total_of),
        )
    )

    @classmethod
    def DEFAULT_READING(cls) -> Specification:
        return Specification.model_validate({"customer_id": {}, "lines": {}})


books = registry(metadata=MetaData())
sale_table = entity_table(
    "previewed_sale",
    books.metadata,
    Column("customer_id", Text, nullable=False),
    Column("discount", Numeric(12, 2), nullable=False),
    Column("subtotal", Numeric(12, 2), nullable=False),
    Column("total", Numeric(12, 2), nullable=False),
)
line_table = entity_table(
    "previewed_sale_line",
    books.metadata,
    Column("sale_id", Text, ForeignKey("previewed_sale.id"), nullable=False),
    Column("product", Text, nullable=False),
    Column("qty", Integer, nullable=False),
    Column("price", Numeric(12, 2), nullable=False),
)
counters = numbering_table("previewed_numbers", books.metadata)
map_aggregates(books, {Sale: sale_table, SaleLine: line_table})


class ResponsePreviewSale(ResponsePreview):
    pass


class QueryPreviewSale(Preview[Sale, ResponsePreviewSale]):
    pass


@pytest.fixture
def database() -> Database:
    fresh = Database("sqlite://")
    books.metadata.create_all(fresh.engine)
    return fresh


def a_stored_sale(store: Repository) -> Sale:
    with store.context() as repository:
        sale = Sale(customer_id="c1")
        sale.lines = [
            SaleLine(sale_id=sale.id, product="cuaderno", qty=10, price=Decimal("12.50")),
            SaleLine(sale_id=sale.id, product="lapiz", qty=4, price=Decimal("2.00")),
        ]
        repository.save(sale)
    return sale


def test_a_save_stores_the_totals_its_lines_make(database):
    store = Repository(database)
    sale = a_stored_sale(store)

    stored = store.get(Sale, sale.id)
    assert stored is not None
    assert (stored.subtotal, stored.total) == (Decimal("133.00"), Decimal("133.00"))


def test_previewing_other_lines_answers_the_new_totals_and_writes_nothing(database):
    store = Repository(database)
    sale = a_stored_sale(store)
    bus = UseFramework("sales-preview", log_after_execution=False)
    bus.add_dependency("repository", store)

    @bus.feature(QueryPreviewSale)
    class SaleReads(EntityReads[Sale]):
        pass

    answer = bus(
        QueryPreviewSale(
            id=sale.id,
            values={
                "discount": "3",
                "lines": [
                    {"sale_id": sale.id, "product": "mochila", "qty": 2, "price": "150"}
                ],
            },
            changed=["lines", "discount"],
        ),
        ResponsePreviewSale,
    )

    assert answer.values == {"subtotal": "300", "total": "297"}
    stored = store.get(Sale, sale.id)
    assert stored is not None and (stored.discount, stored.total) == (
        Decimal("0"),
        Decimal("133.00"),
    )
    assert len(store.search(SaleLine, Criteria())) == 2


@pytest.mark.parametrize(
    "write",
    [
        lambda store, sale: store.save(sale),
        lambda store, sale: store.remove(sale),
        lambda store, sale: store.update_all(Sale, Criteria(), {"discount": Decimal("1")}),
        lambda store, sale: store.remove_all(SaleLine, Criteria()),
        lambda store, sale: store.upsert(Sale(customer_id="c2"), on=("id",)),
    ],
    ids=["save", "remove", "update_all", "remove_all", "upsert"],
)
def test_every_write_door_of_the_database_is_refused_inside_a_preview(database, write):
    store = Repository(database)
    sale = a_stored_sale(store)

    with previewing(), pytest.raises(WriteInPreview):
        write(store, sale)

    assert store.count(Sale).value == 1
    assert store.count(SaleLine).value == 2


def test_a_database_number_is_not_spent_by_a_preview(database):
    numbers = DatabaseNumbering(database, counters)

    with previewing(), pytest.raises(WriteInPreview, match="take"):
        numbers.next_number("sale")

    assert numbers.next_number("sale") == 1


def test_a_draft_is_activated_over_the_stored_sale_and_refused_once_someone_else_saved(
    database,
):
    """A form keeps a discount as a draft, then activates it: the project's Command reads the
    stored sale, refuses a stale draft before touching it, assigns the values and saves — the
    derivations recompute the total on the way."""
    store = Repository(database)
    sale = a_stored_sale(store)
    drafts = InMemoryDrafts(ttl=timedelta(hours=1))
    with store.context() as repository:
        stored = repository.get(Sale, sale.id)
        assert stored is not None
        draft = drafts.keep(f"sale:{sale.id}:ana", {"discount": "33"}, stored.version, 0)

    with store.context() as repository:
        stored = repository.get(Sale, sale.id)
        assert stored is not None
        refuse_stale(draft, stored)
        assign(stored, draft.values)
        repository.save(stored)
    drafts.discard(draft.key)

    with store.context() as repository:
        activated = repository.get(Sale, sale.id)
        assert activated is not None
        assert (activated.discount, activated.total) == (Decimal("33.00"), Decimal("100.00"))

    late = drafts.keep(f"sale:{sale.id}:beto", {"discount": "5"}, activated.version, 0)
    with store.context() as repository:
        someone = repository.get(Sale, sale.id)
        assert someone is not None
        someone.discount = Decimal("1")
        repository.save(someone)
    with store.context() as repository:
        stored = repository.get(Sale, sale.id)
        assert stored is not None
        with pytest.raises(StaleAggregate, match="changed since draft"):
            refuse_stale(late, stored)
        assert stored.discount == Decimal("1.00")


# ---------------------------------------------------------------------------------------------
# What the review found over a database: each case once broken, now held
# ---------------------------------------------------------------------------------------------


@dataclass
class OrderLine(Entity):
    order_id: str
    qty: int = 1
    price: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")

    def amount_of(self) -> Decimal:
        return self.qty * self.price

    derivations = Derivations["OrderLine"](
        lambda o: (Derive(o.amount, depends=(o.qty, o.price), by=OrderLine.amount_of),)
    )


@dataclass
class Order(Entity):
    customer_id: str
    lines: list[OrderLine] = field(default_factory=list)
    discount: Decimal = Decimal("0")
    subtotal: Decimal = Decimal("0")
    total: Decimal = Decimal("0")

    def subtotal_of(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0"))

    def total_of(self) -> Decimal:
        return self.subtotal - self.discount

    derivations = Derivations["Order"](
        lambda o: (
            Derive(o.subtotal, depends=(o.lines,), by=Order.subtotal_of),
            Derive(o.total, depends=(o.subtotal, o.discount), by=Order.total_of),
        )
    )


orders = registry(metadata=MetaData())
order_table = entity_table(
    "reviewed_order",
    orders.metadata,
    Column("customer_id", Text, nullable=False),
    Column("discount", Numeric(12, 2), nullable=False),
    Column("subtotal", Numeric(12, 2), nullable=False),
    Column("total", Numeric(12, 2), nullable=False),
)
order_line_table = entity_table(
    "reviewed_order_line",
    orders.metadata,
    Column("order_id", Text, ForeignKey("reviewed_order.id"), nullable=False),
    Column("qty", Integer, nullable=False),
    Column("price", Numeric(12, 2), nullable=False),
    Column("amount", Numeric(12, 2), nullable=False),
)
map_aggregates(orders, {Order: order_table, OrderLine: order_line_table})


class ResponsePreviewOrder(ResponsePreview):
    pass


class QueryPreviewOrder(Preview[Order, ResponsePreviewOrder]):
    pass


@pytest.fixture
def order_store() -> Repository:
    fresh = Database("sqlite://")
    orders.metadata.create_all(fresh.engine)
    return Repository(fresh)


def a_stored_order(store: Repository) -> Order:
    with store.context() as repository:
        order = Order(customer_id="c1")
        order.lines = [OrderLine(order_id=order.id, qty=2, price=Decimal("10"))]
        repository.save(order)
    return order


def test_the_lines_are_computed_before_the_order_that_sums_them(order_store):
    order = a_stored_order(order_store)

    with order_store.context() as repository:
        kept = repository.get(Order, order.id)
        assert kept is not None
        assert [line.amount for line in kept.lines] == [Decimal("20.00")]
        assert (kept.subtotal, kept.total) == (Decimal("20.00"), Decimal("20.00"))


def test_lines_assigned_from_a_form_are_records_the_database_writes(order_store):
    with order_store.context() as repository:
        order = Order(customer_id="c2")
        assign(order, {"lines": [{"order_id": order.id, "qty": 3, "price": "5"}]})
        repository.save(order)

    with order_store.context() as repository:
        kept = repository.get(Order, order.id)
        assert kept is not None
        assert [(line.qty, line.amount) for line in kept.lines] == [(3, Decimal("15.00"))]
        assert kept.total == Decimal("15.00")


def test_a_record_read_without_its_lines_still_saves_and_keeps_its_totals(order_store):
    order = a_stored_order(order_store)
    read = order_store.search(Order, Criteria()).items[0]

    read.discount = Decimal("1")
    order_store.save(read)

    kept = order_store.search(Order, Criteria()).items[0]
    assert (kept.discount, kept.subtotal, kept.total) == (
        Decimal("1.00"),
        Decimal("20.00"),
        Decimal("19.00"),
    ), "the subtotal over the unread lines is kept; the total is computed from it"
    assert kept.id == order.id


def test_a_preview_reads_the_lines_its_totals_need_even_when_the_detail_names_none(
    order_store,
):
    order = a_stored_order(order_store)
    bus = UseFramework("reviewed-orders", log_after_execution=False)
    bus.add_dependency("repository", order_store)

    @bus.feature(QueryPreviewOrder)
    class OrderReads(EntityReads[Order]):
        pass

    unchanged = bus(QueryPreviewOrder(id=order.id), ResponsePreviewOrder)
    discounted = bus(
        QueryPreviewOrder(id=order.id, values={"discount": "5"}, changed=["discount"]),
        ResponsePreviewOrder,
    )
    same_amount = bus(
        QueryPreviewOrder(id=order.id, values={"discount": "0"}, changed=["discount"]),
        ResponsePreviewOrder,
    )

    assert unchanged.values == {}
    assert discounted.values == {"total": "15.00"}
    assert same_amount.values == {}


@pytest.mark.parametrize("door", ["record_changes", "tracked commit", "session.add"])
def test_no_door_of_a_unit_of_work_writes_inside_a_preview(order_store, door):
    order = a_stored_order(order_store)

    with pytest.raises(WriteInPreview):
        with previewing():
            if door == "tracked commit":
                with order_store.context(writes=Writes.CHANGED) as repository:
                    kept = repository.get(Order, order.id)
                    assert kept is not None
                    kept.discount = Decimal("7")
            else:
                with order_store.context() as repository:
                    if door == "record_changes":
                        kept = repository.get(Order, order.id)
                        assert kept is not None
                        kept.discount = Decimal("9")
                        repository.record_changes(kept)
                    else:
                        repository.session.add(Order(customer_id="by hand"))
                        repository.session.flush()

    stored = order_store.search(Order, Criteria()).items
    assert [(one.customer_id, one.discount) for one in stored] == [("c1", Decimal("0.00"))]


# ---------------------------------------------------------------------------------------------
# A save never reads, and never computes over a relation it does not hold whole
# ---------------------------------------------------------------------------------------------


def two_line_order(store: Repository) -> Order:
    with store.context() as repository:
        order = Order(customer_id="c1")
        order.lines = [
            OrderLine(order_id=order.id, qty=2, price=Decimal("10")),
            OrderLine(order_id=order.id, qty=9, price=Decimal("1")),
        ]
        repository.save(order)
    return order


def test_a_save_over_lines_cut_by_a_specification_keeps_the_stored_totals(order_store):
    order = two_line_order(order_store)
    only_nine = Specification({"lines": Criteria(where=Condition(field="qty", value=9))})
    read = order_store.search(Order, Criteria(specification=only_nine)).items[0]
    assert [line.qty for line in read.lines] == [9]

    read.discount = Decimal("1")
    order_store.save(read)

    kept = order_store.search(Order, Criteria()).items[0]
    assert (kept.discount, kept.subtotal, kept.total) == (
        Decimal("1.00"),
        Decimal("29.00"),
        Decimal("28.00"),
    ), "the subtotal over the cut lines is kept, never 9; the total is computed from it"
    with order_store.context() as repository:
        stored = repository.get(Order, order.id)
        assert stored is not None
        assert len(stored.lines) == 2


@dataclass
class Item(Entity):
    box_id: str
    name: str = ""


@dataclass
class Tag(Entity):
    box_id: str
    label: str = ""


@dataclass
class Box(Entity):
    name: str = ""
    items: list[Item] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)


boxes = registry(metadata=MetaData())
box_table = entity_table("reviewed_box", boxes.metadata, Column("name", Text, nullable=False))
item_table = entity_table(
    "reviewed_item",
    boxes.metadata,
    Column("box_id", Text, ForeignKey("reviewed_box.id"), nullable=False),
    Column("name", Text, nullable=False),
)
tag_table = entity_table(
    "reviewed_tag",
    boxes.metadata,
    Column("box_id", Text, ForeignKey("reviewed_box.id"), nullable=False),
    Column("label", Text, nullable=False),
)
map_aggregates(boxes, {Box: box_table, Item: item_table, Tag: tag_table})


def test_a_save_of_an_aggregate_without_derivations_reads_no_relation():
    database = Database("sqlite://")
    boxes.metadata.create_all(database.engine)
    store = Repository(database)
    with store.context() as repository:
        box = Box(name="b")
        box.items = [Item(box_id=box.id, name="x")]
        box.tags = [Tag(box_id=box.id, label="t")]
        repository.save(box)
    statements: list[str] = []

    def record(connection, cursor, statement, parameters, context, many) -> None:
        statements.append(statement.split()[0])

    with store.context() as repository:
        kept = repository.get(Box, box.id)
        assert kept is not None
        event.listen(database.engine, "before_cursor_execute", record)
        try:
            kept.name = "renamed"
            repository.save(kept)
        finally:
            event.remove(database.engine, "before_cursor_execute", record)

    assert statements == ["UPDATE"]


# ---------------------------------------------------------------------------------------------
# The preview computes as the save does: the lines first
# ---------------------------------------------------------------------------------------------


def order_reads(store: Repository) -> UseFramework:
    bus = UseFramework("reviewed-order-reads", log_after_execution=False)
    bus.add_dependency("repository", store)

    @bus.feature(QueryPreviewOrder)
    class OrderReads(EntityReads[Order]):
        pass

    return bus


def test_a_preview_computes_the_lines_before_the_order_as_the_save_does(order_store):
    order = a_stored_order(order_store)
    bus = order_reads(order_store)
    lines = [{"order_id": order.id, "qty": 3, "price": "5"}]

    existing = bus(
        QueryPreviewOrder(id=order.id, values={"lines": lines}, changed=["lines"]),
        ResponsePreviewOrder,
    )
    fresh = bus(
        QueryPreviewOrder(values={"customer_id": "c9", "lines": lines}, changed=["lines"]),
        ResponsePreviewOrder,
    )
    with order_store.context() as repository:
        saved = Order(customer_id="c9")
        assign(saved, {"lines": lines})
        repository.save(saved)
    stored = order_store.search(Order, Criteria(where=Condition(field="id", value=saved.id)))

    for answer in (existing, fresh):
        assert (Decimal(answer.values["subtotal"]), Decimal(answer.values["total"])) == (
            Decimal("15"),
            Decimal("15"),
        )
    assert (stored.items[0].subtotal, stored.items[0].total) == (
        Decimal("15.00"),
        Decimal("15.00"),
    )


def test_a_new_record_previewed_answers_no_field_the_framework_writes(order_store):
    bus = order_reads(order_store)

    answer = bus(QueryPreviewOrder(values={"customer_id": "c9"}), ResponsePreviewOrder)

    assert {"id", "created_at", "updated_at", "version"}.isdisjoint(answer.values)
    assert answer.values["customer_id"] == "c9"


# ---------------------------------------------------------------------------------------------
# An edited line keeps its row
# ---------------------------------------------------------------------------------------------


def test_a_line_assigned_with_its_id_is_the_stored_line_edited(order_store):
    order = a_stored_order(order_store)
    line_id = order.lines[0].id

    with order_store.context() as repository:
        kept = repository.get(Order, order.id)
        assert kept is not None
        assign(
            kept, {"lines": [{"id": line_id, "order_id": order.id, "qty": 5, "price": "10"}]}
        )
        repository.save(kept)

    with order_store.context() as repository:
        edited = repository.get(Order, order.id)
        assert edited is not None
        assert [(line.id, line.qty, line.amount) for line in edited.lines] == [
            (line_id, 5, Decimal("50.00"))
        ]
        assert edited.total == Decimal("50.00")
    assert order_store.count(OrderLine).value == 1


def test_a_line_assigned_with_its_id_over_an_order_read_without_lines(order_store):
    order = a_stored_order(order_store)
    line_id = order.lines[0].id

    with order_store.context() as repository:
        kept = repository.search(Order, Criteria()).items[0]
        assign(
            kept, {"lines": [{"id": line_id, "order_id": order.id, "qty": 7, "price": "1"}]}
        )
        repository.save(kept)

    with order_store.context() as repository:
        edited = repository.get(Order, order.id)
        assert edited is not None
        assert [(line.qty, line.amount) for line in edited.lines] == [(7, Decimal("7.00"))]
        assert edited.total == Decimal("7.00")
    assert order_store.count(OrderLine).value == 1


def test_a_line_id_the_order_does_not_hold_is_refused_naming_it(order_store):
    order = a_stored_order(order_store)

    with order_store.context() as repository:
        kept = repository.get(Order, order.id)
        assert kept is not None
        with pytest.raises(ContractViolation, match="gone-line"):
            assign(kept, {"lines": [{"id": "gone-line", "order_id": order.id, "qty": 1}]})


def test_an_edited_line_keeps_its_row_in_memory_too():
    store = MemoryRepository()
    order = Order(customer_id="c1")
    order.lines = [OrderLine(order_id=order.id, qty=2, price=Decimal("10"))]
    store.save(order)
    line_id = order.lines[0].id

    kept = store.get(Order, order.id)
    assert kept is not None
    assign(kept, {"lines": [{"id": line_id, "order_id": order.id, "qty": 5, "price": "10"}]})
    store.save(kept)

    edited = store.get(Order, order.id)
    assert edited is not None
    assert [(line.id, line.qty, line.amount) for line in edited.lines] == [
        (line_id, 5, Decimal("50"))
    ]
    assert edited.total == Decimal("50")


# ---------------------------------------------------------------------------------------------
# What else a session could do inside a preview
# ---------------------------------------------------------------------------------------------


def test_a_text_statement_that_writes_is_refused_inside_a_preview(order_store):
    a_stored_order(order_store)

    with pytest.raises(WriteInPreview):
        with previewing():
            with order_store.context() as repository:
                repository.session.execute(text("UPDATE reviewed_order SET discount = 3"))

    with previewing():
        with order_store.context() as repository:
            read = repository.session.execute(text("SELECT count(*) FROM reviewed_order"))
            assert read.scalar() == 1
    assert order_store.search(Order, Criteria()).items[0].discount == Decimal("0.00")


def test_a_read_that_locks_rows_is_refused_inside_a_preview(order_store):
    order = a_stored_order(order_store)

    with pytest.raises(WriteInPreview, match="locks"):
        with previewing():
            with order_store.context() as repository:
                repository.get(Order, order.id, for_update=True)


# ---------------------------------------------------------------------------------------------
# A save over a relation held in part, and the guard's last doors
# ---------------------------------------------------------------------------------------------


def _warnings(logs: list) -> list[str]:
    return [line["event"] for line in logs if line["log_level"] == "warning"]


def test_a_save_over_cut_lines_with_an_edited_line_keeps_the_totals_and_warns(order_store):
    order = two_line_order(order_store)
    only_nine = Specification({"lines": Criteria(where=Condition(field="qty", value=9))})
    read = order_store.search(Order, Criteria(specification=only_nine)).items[0]
    read.lines[0].qty = 3

    with capture_logs() as logs:
        order_store.save(read)

    with order_store.context() as repository:
        stored = repository.get(Order, order.id)
        assert stored is not None
        assert sorted(line.amount for line in stored.lines) == [
            Decimal("3.00"),
            Decimal("20.00"),
        ]
        assert (stored.subtotal, stored.total) == (Decimal("29.00"), Decimal("29.00"))
    warned = _warnings(logs)
    assert len(warned) == 1
    assert "Order.subtotal" in warned[0] and "Order.lines" in warned[0] and "cut" in warned[0]
    assert "context()" in warned[0]


def test_a_save_over_lines_replaced_blind_keeps_the_totals_and_warns(order_store):
    order = a_stored_order(order_store)
    read = order_store.search(Order, Criteria()).items[0]
    read.lines = [OrderLine(order_id=order.id, qty=3, price=Decimal("5"))]

    with capture_logs() as logs:
        order_store.save(read)

    kept = order_store.search(Order, Criteria()).items[0]
    assert kept.subtotal == Decimal("20.00")
    about_the_total = [one for one in _warnings(logs) if "Order.subtotal" in one]
    assert len(about_the_total) == 1, "beside the cascade's own warning about the blind set"
    assert "held blind" in about_the_total[0]


def test_a_save_over_lines_never_read_stays_silent(order_store):
    a_stored_order(order_store)
    read = order_store.search(Order, Criteria()).items[0]
    read.discount = Decimal("1")

    with capture_logs() as logs:
        order_store.save(read)

    assert _warnings(logs) == []
    assert order_store.search(Order, Criteria()).items[0].total == Decimal("19.00")


def test_assign_without_ids_over_lines_never_read_replaces_them_as_an_assignment_does(
    order_store,
):
    order = a_stored_order(order_store)
    read = order_store.search(Order, Criteria()).items[0]

    changed = assign(read, {"lines": [{"order_id": order.id, "qty": 3, "price": "5"}]})

    assert changed == ("lines",)
    assert [line.qty for line in read.lines] == [3]


@pytest.mark.parametrize(
    "statement",
    [
        "WITH doomed AS (SELECT id FROM reviewed_order) "
        "UPDATE reviewed_order SET discount = 99 WHERE id IN (SELECT id FROM doomed)",
        "SELECT * FROM reviewed_order FOR UPDATE",
        "SELECT 1; DELETE FROM reviewed_order",
        "  -- a comment\nDELETE FROM reviewed_order",
    ],
    ids=["with-update", "for-update", "two-statements", "comment-then-delete"],
)
def test_a_text_statement_that_writes_or_locks_in_disguise_is_refused(order_store, statement):
    a_stored_order(order_store)

    with pytest.raises(WriteInPreview):
        with previewing():
            with order_store.context() as repository:
                repository.session.execute(text(statement))

    assert order_store.search(Order, Criteria()).items[0].discount == Decimal("0.00")


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT count(*) FROM reviewed_order",
        "  -- a comment\nSELECT count(*) FROM reviewed_order",
        "/* a comment */ WITH n AS (SELECT id FROM reviewed_order) SELECT count(*) FROM n",
        "SELECT count(*) FROM reviewed_order;",
    ],
    ids=["select", "comment-select", "with-select", "trailing-semicolon"],
)
def test_a_text_statement_that_only_reads_runs_inside_a_preview(order_store, statement):
    a_stored_order(order_store)

    with previewing():
        with order_store.context() as repository:
            assert repository.session.execute(text(statement)).scalar() == 1


def test_a_preview_inside_a_unit_of_work_with_pending_changes_reads_without_flushing(
    order_store,
):
    order = a_stored_order(order_store)

    with order_store.context(writes=Writes.CHANGED) as repository:
        stored = repository.get(Order, order.id)
        assert stored is not None
        stored.discount = Decimal("4")
        assert repository.session.dirty, "a change the unit of work will write at its commit"
        with previewing():
            seen = repository.search(Order, Criteria()).items
        assert len(seen) == 1

    assert order_store.search(Order, Criteria()).items[0].discount == Decimal("4.00")


def test_the_reading_test_is_one_statement_that_reads_and_locks_nothing():
    assert _reads_only("(SELECT 1) UNION (SELECT 2)")
    assert _reads_only("select 1 -- trailing note")
    assert not _reads_only("SELECT * FROM t FOR NO KEY UPDATE")
    assert not _reads_only("SELECT * FROM t for share")
    assert not _reads_only("WITH x AS (DELETE FROM t RETURNING id) SELECT * FROM x")
    assert not _reads_only("select nextval('s'); select 1")
    assert not _reads_only("")
