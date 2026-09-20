"""CASE 3 — a database each.

Nothing is shared. No reference between contexts can be a key, no report can be a join, and no
transaction spans two of them. Everything one context knows about another it was **told**, and
kept for itself.

The five things every case is measured on:

    1. relations       a key only inside a context; everything else is asked or told
    2. propagation     the only way anything crosses
    3. write + publish the outbox stops being optional
    4. projections     a context whose entire state arrived from elsewhere
    5. sagas           the failure mode this shape makes unavoidable

**What this case costs, and what it buys.** It costs every join and every cross-context
transaction. It buys the thing the other two cannot have: a context that keeps working, and
keeps answering, while another one is down.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from sincpro_framework.ddd.criteria import Condition, Criteria, Sort
from sincpro_framework.ddd.events import EventStatus

from ..shop import billing, inventory, notifications, orders
from ..shop.contracts import InvoiceIssued, OrderPlaced, StockRejected, StockReserved
from ..stories import an_order, deliver, stocked, timeline
from ..wiring import Shop, distributed

# --- 1. nothing is shared ---------------------------------------------------------------------


def test_no_two_contexts_share_a_database(four_databases: Shop):
    names = list(four_databases.databases)
    for one in names:
        for another in names:
            if one != another:
                assert not four_databases.shares_a_database(one, another)


def test_the_key_inside_a_context_is_untouched_by_any_of_this(four_databases: Shop):
    """The shape decides what is *between* contexts. Inside one, an aggregate and the aggregate
    it owns are in one schema and joined by a real foreign key, exactly as in case 1."""
    stocked(four_databases)
    placed = an_order(four_databases, quantity=3)

    with four_databases.store("orders").context() as unit:
        order = unit.get(orders.Order, placed.order_id)
        assert order is not None
        assert order.lines[0].quantity == 3


def test_not_one_of_the_other_contexts_tables_is_reachable(four_databases: Shop):
    """Four engines, four schemas. A query across them is not discouraged — it is impossible,
    which is the only kind of boundary that holds under deadline."""
    for name, missing in (
        ("orders", "shop_invoice"),
        ("billing", "shop_stock"),
        ("inventory", "shop_told"),
        ("notifications", "shop_order"),
    ):
        with four_databases.store(name).database.session() as session:
            with pytest.raises(OperationalError, match="no such table"):
                session.execute(sa.text(f"SELECT * FROM {missing}")).all()


def test_a_reference_across_contexts_is_a_question_somebody_answers(
    four_databases: Shop,
):
    """`billing` needs what only `orders` knows and asks for it — the same command as in case 2,
    unchanged. That is the point: the reference that works everywhere is the one that was never
    a key."""
    stocked(four_databases)
    placed = an_order(four_databases, customer="Bruno", quantity=2, price=75)

    told = four_databases["orders"](
        orders.CommandTellAboutOrder(order_id=placed.order_id),
        orders.ResponseAboutOrder,
    )

    assert told.customer == "Bruno" and told.total == 150


# --- 2. propagation is the only way anything crosses -------------------------------------------


def test_every_context_learned_what_it_knows_by_being_told(four_databases: Shop):
    """Four databases, and each holds a record of the same order — none of them by reading
    another's table."""
    stocked(four_databases)
    placed = an_order(four_databases)
    deliver(four_databases)

    assert len(orders.history_of(four_databases.store("orders"), placed.order_id)) == 3
    assert len(inventory.history_of(four_databases.store("inventory"), placed.order_id)) == 1
    assert four_databases.store("billing").count(billing.Invoices).value == 1
    assert timeline(four_databases, placed.order_id).steps == 3


def test_the_same_fact_is_kept_four_times_and_every_copy_is_right(four_databases: Shop):
    """**The claim this whole package exists to make.** `StockReserved` is written down by
    `inventory` because it happened there, by `orders` because an order's history is its
    question, and by `notifications` because the timeline *is* its state. Three databases, three
    rows, three different shapes, and not one of them is a duplicate of another."""
    stocked(four_databases)
    placed = an_order(four_databases)

    in_inventory = inventory.history_of(four_databases.store("inventory"), placed.order_id)
    in_orders = orders.history_of(four_databases.store("orders"), placed.order_id)
    in_notifications = four_databases.store("notifications").fetch_all(
        notifications.Tolds,
        Criteria(
            where=Condition(field="said", value=StockReserved.name),
            order=(Sort(field="id"),),
        ),
    )

    assert [one.said for one in in_inventory] == [StockReserved.name]
    assert StockReserved.name in [one.said for one in in_orders]
    assert len(in_notifications) == 1
    # Same fact, different shapes: one keeps the sku, one keeps a detail, one keeps only that
    # it was told.
    assert in_inventory[0].sku == "MUG"
    assert in_notifications[0].order_id == placed.order_id


def test_a_context_answers_from_its_own_database_alone(four_databases: Shop):
    """The property the shape is bought for: `orders` can answer "what happened to this order"
    with every other engine unreachable."""
    stocked(four_databases)
    placed = an_order(four_databases)
    deliver(four_databases)

    for name in ("billing", "inventory", "notifications"):
        four_databases.store(name).database.engine.dispose()

    said = [
        one.said for one in orders.history_of(four_databases.store("orders"), placed.order_id)
    ]
    assert said == [OrderPlaced.name, StockReserved.name, InvoiceIssued.name]


# --- 3. the outbox stops being optional --------------------------------------------------------


def test_the_fact_and_the_invoice_commit_together_and_nothing_else_can(
    four_databases: Shop,
):
    """There is no transaction that covers the invoice and the telling of it — they are in
    different databases. What *can* be made atomic is the invoice and the **intent** to tell,
    and that is the whole idea of an outbox."""
    stocked(four_databases)
    an_order(four_databases)
    store = four_databases.store("billing")

    assert store.count(billing.Invoices).value == 1
    assert store.count(billing.Outboxes, billing.PENDING).value == 1
    assert (
        four_databases.store("notifications")
        .count(
            notifications.Tolds,
            Criteria(where=Condition(field="said", value=InvoiceIssued.name)),
        )
        .value
        == 0
    )


def test_a_crash_before_the_relay_loses_nothing(four_databases: Shop):
    """The window the outbox closes. The process ends between the commit and the delivery; the
    fact is still sitting in the database, pending, and the next relay sends it."""
    stocked(four_databases)
    placed = an_order(four_databases)
    # ...nothing delivered it. A new process starts and runs the relay.

    assert deliver(four_databases) == 1

    assert timeline(four_databases, placed.order_id).steps == 3


def test_the_relay_is_idempotent(four_databases: Shop):
    stocked(four_databases)
    placed = an_order(four_databases)

    assert deliver(four_databases) == 1
    assert deliver(four_databases) == 0
    assert timeline(four_databases, placed.order_id).steps == 3


def test_a_delivered_fact_keeps_its_delivery_state(four_databases: Shop):
    stocked(four_databases)
    an_order(four_databases)
    deliver(four_databases)

    [sent] = four_databases.store("billing").fetch_all(billing.Outboxes)
    assert sent.status == EventStatus.ACKNOWLEDGED


# --- 4. a context whose entire state arrived from elsewhere -------------------------------------


def test_notifications_holds_nothing_but_facts(four_databases: Shop):
    """No order, no invoice, no stock — and it can still answer what happened to an order. Its
    database has exactly one table."""
    stocked(four_databases)
    placed = an_order(four_databases)
    deliver(four_databases)

    assert list(notifications.metadata.tables) == ["shop_told"]
    assert timeline(four_databases, placed.order_id).steps == 3


def test_the_projection_is_derived_and_therefore_disposable(four_databases: Shop):
    """Folded again from the same facts, it comes out the same. That is what makes a projection
    safe to throw away and rebuild — it holds no truth of its own."""
    stocked(four_databases)
    placed = an_order(four_databases)
    deliver(four_databases)

    assert timeline(four_databases, placed.order_id) == timeline(
        four_databases, placed.order_id
    )


# --- 5. the failure this shape makes unavoidable -------------------------------------------------


def test_a_step_that_fails_leaves_every_earlier_step_committed(four_databases: Shop):
    """Four databases and no transaction over any two of them. `inventory` reserved and
    committed; `billing` refuses. Nothing rolls back, because there is nothing that could."""
    shop = distributed(billing_refuses=True)
    stocked(shop)

    with pytest.raises(billing.RefusedToInvoice):
        an_order(shop, quantity=3)

    assert inventory.on_hand(shop.store("inventory"), "MUG") == 7
    assert shop.store("billing").count(billing.Invoices).value == 0
    assert shop.store("orders").count(orders.Orders).value == 1  # the order stands


def test_the_compensation_is_a_command_and_leaves_its_own_trace(four_databases: Shop):
    """Undoing is a new fact. The reservation happened and the release happened, and the log
    says both — which is what an audit needs and an erasure could never give."""
    shop = distributed(billing_refuses=True)
    stocked(shop)
    try:
        an_order(shop, quantity=3)
    except billing.RefusedToInvoice:
        pass
    order_id = shop.store("orders").fetch_all(orders.Orders)[0].id

    shop["inventory"](
        inventory.CommandReleaseStock(order_id=order_id, sku="MUG", quantity=3),
        inventory.ResponseHeard,
    )

    assert inventory.on_hand(shop.store("inventory"), "MUG") == 10
    assert [one.said for one in inventory.history_of(shop.store("inventory"), order_id)] == [
        StockReserved.name,
        "inventory.v1.released",
    ]


def test_a_refusal_that_needs_no_compensation(four_databases: Shop):
    """The cheap failure: the step never happened, so there is nothing to undo. `inventory`
    says no, `orders` moves the order, and no stock moved at all."""
    stocked(four_databases, on_hand=1)
    placed = an_order(four_databases, quantity=5)

    order = four_databases.store("orders").get(orders.Order, placed.order_id)
    assert order is not None and order.state == "rejected: out of stock"
    assert inventory.on_hand(four_databases.store("inventory"), "MUG") == 1
    assert four_databases.store("billing").count(billing.Invoices).value == 0
    said = [
        one.said
        for one in four_databases.store("notifications").fetch_all(notifications.Tolds)
    ]
    assert StockRejected.name in said


def test_a_fact_nobody_can_deliver_gives_up_saying_so(four_databases: Shop):
    """**The dead letter, with no machinery of its own.** A consumer that is down makes every
    delivery fail. `mark_processing` counts the attempt, `mark_failed` records why, and past a
    cap the relay stops trying — `mark_cancelled`, which no later claim picks up.

    Retrying forever is how a broken consumer takes the producer down with it. Giving up
    silently is how a fact disappears. This gives up *and says so*, and the row is still there
    to look at.
    """
    GIVE_UP_AFTER = 3
    stocked(four_databases)
    an_order(four_databases)
    store = four_databases.store("billing")

    def relay_that_keeps_failing() -> None:
        with store.context() as unit:
            claimed = list(unit.search(billing.Outboxes, billing.PENDING, for_update=True))
            for one in claimed:
                one.mark_processing()
                unit.save(one)
        for one in claimed:
            with store.context() as unit:
                if one.attempts >= GIVE_UP_AFTER:
                    one.mark_cancelled()
                else:
                    one.mark_failed("the consumer is down")
                    one.status = EventStatus.PENDING  # back in the queue
                unit.save(one)

    seen = []
    for _ in range(4):
        relay_that_keeps_failing()
        [row] = store.fetch_all(billing.Outboxes)
        seen.append((row.attempts, row.status))

    assert seen == [
        (1, EventStatus.PENDING),
        (2, EventStatus.PENDING),
        (3, EventStatus.CANCELLED),
        (3, EventStatus.CANCELLED),  # nobody claims it again
    ]
    [undelivered] = store.fetch_all(billing.Outboxes)
    assert timeline(four_databases, undelivered.order_id).steps == 2  # never invoiced
