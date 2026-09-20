"""CASE 1 — everything in one database.

Four bounded contexts, one engine. Every table is reachable from every other, so this is the
shape where the *most* is possible: a foreign key the engine enforces, a join in one query, a
write across two contexts in one transaction.

The five things every case is measured on:

    1. relations       how a reference is followed — by `Criteria`, and by attribute
    2. propagation     how a fact reaches another context
    3. write + publish what commits together and what is announced afterwards
    4. projections     a read model kept from facts, rather than rebuilt each time
    5. sagas           a step that fails, and the compensation that follows it

**What this case is really for**: showing what one database buys, and what it costs. It buys
foreign keys and one transaction. It costs the ability to deploy or scale one context alone —
and the discipline that keeps a context out of another's tables is now yours, not the schema's.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from sincpro_framework.ddd.criteria import Condition, Criteria, Sort
from sincpro_framework.ddd.exceptions import ContractViolation

from ..shop import billing, inventory, notifications, orders
from ..shop.contracts import InvoiceIssued, OrderPlaced, StockRejected, StockReserved
from ..stories import an_order, deliver, stocked, timeline
from ..wiring import Shop, monolith

# --- 1. relations: by criteria, and by attribute --------------------------------------------


def test_two_aggregates_of_one_context_are_joined_by_a_real_foreign_key(one_database: Shop):
    """`Order` and `OrderLine` share a schema, so the column declares the key, the engine
    enforces it, and the relation is inferred — nothing is declared twice."""
    stocked(one_database)
    placed = an_order(one_database, quantity=3, price=50)

    with one_database.store("orders").context() as unit:
        order = unit.get(orders.Order, placed.order_id)
        assert order is not None
        assert [one.sku for one in order.lines] == ["MUG"]
        assert order.lines[0].quantity == 3


def test_a_relation_is_followed_by_attribute_inside_a_unit_of_work(one_database: Shop):
    """`order.lines[0].sku` — the plain object graph, resolved on first touch. No query is
    written by the caller, and none is issued until the attribute is read."""
    stocked(one_database)
    placed = an_order(one_database)

    with one_database.store("orders").context() as unit:
        order = unit.get(orders.Order, placed.order_id)
        assert order is not None
        assert order.lines[0].price == 50  # the graph, not a query


def test_the_same_reference_read_by_criteria_instead(one_database: Shop):
    """The other door: ask for the lines directly. What a screen with a filter and a page
    does, and the only one that can be pushed down to the engine."""
    stocked(one_database)
    placed = an_order(one_database)

    lines = one_database.store("orders").fetch_all(
        orders.OrderLines,
        Criteria(where=Condition(field="order_id", value=placed.order_id)),
    )

    assert [one.sku for one in lines] == ["MUG"]


def test_outside_a_unit_of_work_a_relation_refuses_rather_than_reading_wide(
    one_database: Shop,
):
    """The N+1 the layer exists to prevent: a relation nobody asked for is not quietly fetched
    per record."""
    from sincpro_framework.ddd.exceptions import RelationNotResolved

    stocked(one_database)
    placed = an_order(one_database)
    order = one_database.store("orders").get(orders.Order, placed.order_id)
    assert order is not None

    with pytest.raises(RelationNotResolved, match="was not asked for"):
        order.lines  # noqa: B018


def test_the_engine_refuses_a_line_pointing_at_an_order_that_does_not_exist(
    one_database: Shop,
):
    """What a foreign key buys and a reference across contexts cannot: the database says no."""
    with one_database.store("orders").database.session() as session:
        session.execute(sa.text("PRAGMA foreign_keys=ON"))
        with pytest.raises(IntegrityError):
            session.execute(
                sa.text(
                    "INSERT INTO shop_order_line"
                    " (id, created_at, version, order_id, sku, quantity, price)"
                    " VALUES ('x', '2026-01-01', 1, 'nobody', 'MUG', 1, 1)"
                )
            )


def test_one_database_can_answer_across_two_contexts_in_one_query(one_database: Shop):
    """**What only this shape can do.** `shop_order` and `shop_stock` belong to different
    contexts and are in one engine, so a report over both is a join. In case 2 and case 3 this
    query cannot be written at all — the tables are not in the same place."""
    stocked(one_database, on_hand=10)
    an_order(one_database, quantity=3)

    with one_database.store("orders").database.session() as session:
        answered = session.execute(
            sa.text(
                "SELECT o.customer, s.sku, s.on_hand"
                " FROM shop_order o JOIN shop_order_line l ON l.order_id = o.id"
                " JOIN shop_stock s ON s.sku = l.sku"
            )
        ).all()

    assert answered == [("Ana", "MUG", 7)]


# --- 2. propagation: how a fact reaches another context -------------------------------------


def test_one_fact_reaches_every_context_that_asked_for_it(one_database: Shop):
    """`OrderPlaced` is published once and heard by two contexts — `inventory` reserves,
    `notifications` writes it down. Neither knows the other exists."""
    stocked(one_database)
    placed = an_order(one_database, quantity=3)

    assert inventory.on_hand(one_database.store("inventory"), "MUG") == 7
    assert timeline(one_database, placed.order_id).steps >= 1


def test_a_fact_sets_off_the_next_one(one_database: Shop):
    """placed → reserved → invoiced. Each context answers the fact before it and says its own;
    nobody orchestrates the chain."""
    stocked(one_database)
    placed = an_order(one_database)
    deliver(one_database)

    said = [
        one.said for one in orders.history_of(one_database.store("orders"), placed.order_id)
    ]

    assert said == [OrderPlaced.name, StockReserved.name, InvoiceIssued.name]


def test_each_context_keeps_its_own_record_of_the_same_fact(one_database: Shop):
    """Three contexts wrote down that stock was reserved, each in its own table and its own
    shape. **Sharing one database changes nothing about this**: a log belongs to the context
    whose question it answers, not to the schema."""
    stocked(one_database)
    placed = an_order(one_database)

    in_orders = [
        one.said for one in orders.history_of(one_database.store("orders"), placed.order_id)
    ]
    in_inventory = [
        one.said
        for one in inventory.history_of(one_database.store("inventory"), placed.order_id)
    ]

    assert StockReserved.name in in_orders
    assert in_inventory == [StockReserved.name]
    assert timeline(one_database, placed.order_id).steps == 2


# --- 3. the write and the fact ---------------------------------------------------------------


def test_the_order_its_line_and_the_fact_commit_together(one_database: Shop):
    """One transaction. Either all three landed or none did."""
    stocked(one_database)
    placed = an_order(one_database)
    store = one_database.store("orders")

    assert store.count(orders.Orders).value == 1
    assert store.count(orders.OrderLines).value == 1
    assert store.get(orders.Order, placed.order_id) is not None


def test_nothing_is_announced_from_inside_the_write(one_database: Shop):
    """`billing` writes its invoice and its outbox row together and publishes nothing. Until
    the relay runs, no other context has heard of the invoice — which is exactly the window a
    crash would otherwise lose."""
    stocked(one_database)
    placed = an_order(one_database)

    assert one_database.store("billing").count(billing.Invoices).value == 1
    assert timeline(one_database, placed.order_id).steps == 2  # placed and reserved

    assert deliver(one_database) == 1

    assert timeline(one_database, placed.order_id).steps == 3  # and now invoiced


def test_an_aggregate_that_refuses_leaves_nothing_behind(one_database: Shop):
    stocked(one_database)
    placed = an_order(one_database)
    store = one_database.store("orders")
    order = store.get(orders.Order, placed.order_id)
    assert order is not None
    order.refused("first time")
    store.save(order)

    with pytest.raises(ContractViolation, match="cannot be refused"):
        order.refused("again")


# --- 4. a projection kept from facts ---------------------------------------------------------


def test_a_timeline_is_rebuilt_from_facts_and_never_stored(one_database: Shop):
    """`notifications` has no row for "the state of an order". Its state is the facts, folded
    in the order they were written down — `id` is a UUID v7, so ordering by it is ordering by
    time."""
    stocked(one_database)
    placed = an_order(one_database)
    deliver(one_database)

    told = one_database.store("notifications").fetch_all(
        notifications.Tolds,
        Criteria(
            where=Condition(field="order_id", value=placed.order_id),
            order=(Sort(field="id"),),
        ),
    )

    assert sorted(one.said for one in told) == sorted(
        [OrderPlaced.name, StockReserved.name, InvoiceIssued.name]
    )


def test_a_synchronous_queue_delivers_depth_first_and_the_order_shows_it(
    one_database: Shop,
):
    """**Measured, because it decides whether a projection can trust the order it reads.**

    `SyncQueue` runs each subscriber in the call. `inventory` is registered before
    `notifications`, and while handling `OrderPlaced` it publishes `StockReserved` — which
    re-enters the queue immediately. So `notifications` is told about the *inner* fact first:

        notifications   reserved, placed, invoiced
        orders          placed, reserved, invoiced

    `orders` reads correctly because it writes `OrderPlaced` into its own log inside its own
    transaction, before publishing anything — it never learns that fact from the queue.

    A projection that folds by order needs the facts in the order they happened. Two ways to
    get it, both used in this package: **write the fact down where it happened**, as `orders`
    does, or **go through an outbox**, as `billing` does — a relay delivers what is pending, in
    `id` order, outside anybody's call stack.
    """
    stocked(one_database)
    placed = an_order(one_database)

    heard = [
        one.said
        for one in one_database.store("notifications").fetch_all(
            notifications.Tolds,
            Criteria(
                where=Condition(field="order_id", value=placed.order_id),
                order=(Sort(field="id"),),
            ),
        )
    ]
    written_where_it_happened = [
        one.said for one in orders.history_of(one_database.store("orders"), placed.order_id)
    ]

    assert heard == [StockReserved.name, OrderPlaced.name]
    assert written_where_it_happened == [OrderPlaced.name, StockReserved.name]


def test_the_same_facts_can_be_kept_as_a_materialised_view(one_database: Shop):
    """Rebuilding on every read does not scale, so the answer is kept beside the facts — an
    ordinary aggregate, saved like any other, derived and therefore disposable. Here it is
    written to a table and read back without folding anything."""
    stocked(one_database)
    placed = an_order(one_database)
    deliver(one_database)
    store = one_database.store("notifications")

    folded = notifications.timeline_of(store, placed.order_id)
    with store.database.session() as session:
        session.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS order_status"
                " (order_id TEXT PRIMARY KEY, status TEXT, steps INTEGER)"
            )
        )
        session.execute(
            sa.text("INSERT OR REPLACE INTO order_status VALUES (:o, :s, :n)"),
            {"o": folded.order_id, "s": folded.status, "n": folded.steps},
        )

    with store.database.session() as session:
        [row] = session.execute(sa.text("SELECT status, steps FROM order_status")).all()
    assert row == ("invoiced", 3)


def test_the_view_can_be_thrown_away_and_rebuilt(one_database: Shop):
    """The property that makes a projection safe: it holds no truth of its own. The facts do."""
    stocked(one_database)
    placed = an_order(one_database)
    deliver(one_database)

    first = timeline(one_database, placed.order_id)
    again = timeline(one_database, placed.order_id)

    assert first == again and first.steps == 3  # nothing was consumed by reading it


# --- 5. a saga: a step fails, and something has to undo it ------------------------------------


def test_a_failing_step_does_not_roll_back_what_already_happened(one_database: Shop):
    """**The thing a saga exists for.** `inventory` committed its reservation before `billing`
    was ever asked. There is no transaction spanning the two — even here, in one database,
    because each context ran in its own unit of work. A failure downstream leaves the earlier
    step standing."""
    shop = monolith(billing_refuses=True)
    stocked(shop)

    with pytest.raises(billing.RefusedToInvoice):
        an_order(shop, quantity=3)

    assert inventory.on_hand(shop.store("inventory"), "MUG") == 7  # still reserved
    assert shop.store("billing").count(billing.Invoices).value == 0


def test_the_compensation_is_another_step_and_not_a_rollback(one_database: Shop):
    """Undoing a committed step is a new fact, not an erasure. The reservation really happened;
    so does the release, and both are in the log."""
    shop = monolith(billing_refuses=True)
    stocked(shop)
    try:
        an_order(shop, quantity=3)
    except billing.RefusedToInvoice:
        pass
    placed_id = shop.store("orders").fetch_all(orders.Orders)[0].id

    shop["inventory"](
        inventory.CommandReleaseStock(order_id=placed_id, sku="MUG", quantity=3),
        inventory.ResponseHeard,
    )

    assert inventory.on_hand(shop.store("inventory"), "MUG") == 10  # put back
    said = [one.said for one in inventory.history_of(shop.store("inventory"), placed_id)]
    assert said == [StockReserved.name, "inventory.v1.released"]


def test_an_order_nobody_could_stock_is_refused_and_says_so(one_database: Shop):
    """The other failure, and the one that needs no compensation: the step never happened.
    `inventory` answers `StockRejected`, and `orders` moves the order itself."""
    stocked(one_database, on_hand=1)
    placed = an_order(one_database, quantity=5)

    order = one_database.store("orders").get(orders.Order, placed.order_id)
    assert order is not None and order.state == "rejected: out of stock"
    assert inventory.on_hand(one_database.store("inventory"), "MUG") == 1  # untouched
    assert one_database.store("billing").count(billing.Invoices).value == 0
    said = [
        one.said for one in one_database.store("notifications").fetch_all(notifications.Tolds)
    ]
    assert StockRejected.name in said
