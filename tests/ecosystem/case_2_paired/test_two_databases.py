"""CASE 2 — two databases, two contexts each.

    orders + inventory        share one
    billing + notifications   share the other

**The shape a system actually reaches**, and the interesting one: it is transactional in one
place and eventually consistent in another, at the same time, with the same domain code. Case 1
and case 3 are its two extremes.

The five things every case is measured on:

    1. relations       a key inside a pair; a command across the pairs
    2. propagation     a fact crosses where a key cannot
    3. write + publish what commits together stops at the pair's edge
    4. projections     a read model built from facts that arrived from elsewhere
    5. sagas           a failure on the far side of the boundary, and its compensation

Nothing under `shop/` changed to run this way. Only which engine a repository points at.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from sincpro_framework.ddd.criteria import Condition, Criteria, Sort

from ..shop import billing, inventory, notifications, orders
from ..shop.contracts import InvoiceIssued, OrderPlaced, StockRejected, StockReserved
from ..stories import an_order, deliver, stocked, timeline
from ..wiring import Shop, paired

# --- 1. what a pair shares, and what it does not --------------------------------------------


def test_the_pairs_are_what_the_shape_says_they_are(two_databases: Shop):
    assert two_databases.shares_a_database("orders", "inventory")
    assert two_databases.shares_a_database("billing", "notifications")
    assert not two_databases.shares_a_database("orders", "billing")
    assert not two_databases.shares_a_database("inventory", "notifications")


def test_inside_one_context_the_key_is_still_a_foreign_key(two_databases: Shop):
    """`Order` and `OrderLine` are in one schema wherever the pair lives. Nothing about the
    shape reaches down this far."""
    stocked(two_databases)
    placed = an_order(two_databases, quantity=3)

    with two_databases.store("orders").context() as unit:
        order = unit.get(orders.Order, placed.order_id)
        assert order is not None
        assert order.lines[0].sku == "MUG"


def test_two_contexts_in_one_pair_can_be_read_together(two_databases: Shop):
    """`orders` and `inventory` share an engine, so a report across them is a join — the same
    query case 1 could write, still available here."""
    stocked(two_databases, on_hand=10)
    an_order(two_databases, quantity=3)

    with two_databases.store("orders").database.session() as session:
        [row] = session.execute(
            sa.text(
                "SELECT o.customer, s.on_hand FROM shop_order o"
                " JOIN shop_order_line l ON l.order_id = o.id"
                " JOIN shop_stock s ON s.sku = l.sku"
            )
        ).all()

    assert row == ("Ana", 7)


def test_across_the_pairs_the_tables_are_not_even_there(two_databases: Shop):
    """**The line the shape draws.** `billing`'s engine has no `shop_order` table, so the join
    above cannot be written at all — not "should not", cannot."""
    with two_databases.store("billing").database.session() as session:
        with pytest.raises(OperationalError, match="no such table"):
            session.execute(sa.text("SELECT * FROM shop_order")).all()


def test_across_the_pairs_a_reference_is_a_question_somebody_answers(
    two_databases: Shop,
):
    """**How the other side of the boundary is reached.** `billing` needs the customer and the
    total, which only `orders` knows. It asks — a command on that bus — and `orders` answers.
    No join, no import, and it reads the same whether or not they share an engine."""
    stocked(two_databases)
    placed = an_order(two_databases, customer="Bruno", quantity=2, price=75)

    told = two_databases["orders"](
        orders.CommandTellAboutOrder(order_id=placed.order_id),
        orders.ResponseAboutOrder,
    )

    assert told.customer == "Bruno" and told.total == 150
    [invoice] = two_databases.store("billing").fetch_all(billing.Invoices)
    assert invoice.customer == "Bruno" and invoice.total == 150


# --- 2. and 3. a fact crosses where a key cannot ---------------------------------------------


def test_a_fact_crosses_the_pair_boundary_and_a_key_never_could(two_databases: Shop):
    """`OrderPlaced` reaches `notifications`, which is in the other pair. Nothing was joined and
    nothing was imported: the fact carried what the far side needed."""
    stocked(two_databases)
    placed = an_order(two_databases)

    assert timeline(two_databases, placed.order_id).steps >= 1
    assert two_databases.store("notifications").count(notifications.Tolds).value >= 1


def test_what_commits_together_stops_at_the_pair(two_databases: Shop):
    """`billing` writes its invoice and its outbox row in one transaction — both are in its
    own database. The order they are about is in the other one, and no transaction spans them.
    """
    stocked(two_databases)
    an_order(two_databases)
    store = two_databases.store("billing")

    assert store.count(billing.Invoices).value == 1
    assert store.count(billing.Outboxes, billing.PENDING).value == 1

    assert deliver(two_databases) == 1
    assert store.count(billing.Outboxes, billing.PENDING).value == 0


def test_each_side_keeps_its_own_record_of_the_same_fact(two_databases: Shop):
    """`orders` and `notifications` are in different databases and both wrote down that stock
    was reserved. **Neither is a copy**: each kept what answers its own question, and each
    answers it without reaching across."""
    stocked(two_databases)
    placed = an_order(two_databases)

    here = [
        one.said for one in orders.history_of(two_databases.store("orders"), placed.order_id)
    ]
    there = [
        one.said
        for one in two_databases.store("notifications").fetch_all(notifications.Tolds)
    ]

    assert StockReserved.name in here
    assert StockReserved.name in there
    assert (
        two_databases.store("orders").database
        is not two_databases.store("notifications").database
    )


# --- 4. a projection over facts that arrived from elsewhere -----------------------------------


def test_the_timeline_is_folded_from_facts_that_crossed(two_databases: Shop):
    """`notifications` holds no order and no invoice — only what it was told. Its whole state
    came across the boundary."""
    stocked(two_databases)
    placed = an_order(two_databases)
    deliver(two_databases)

    folded = timeline(two_databases, placed.order_id)

    assert folded.steps == 3
    assert sorted(
        one.said
        for one in two_databases.store("notifications").fetch_all(
            notifications.Tolds,
            Criteria(where=Condition(field="order_id", value=placed.order_id)),
        )
    ) == sorted([OrderPlaced.name, StockReserved.name, InvoiceIssued.name])


def test_the_invoice_fact_arrives_in_order_because_it_came_through_the_outbox(
    two_databases: Shop,
):
    """A relay delivers what is pending in `id` order, from outside anybody's call stack — so
    unlike a fact published from inside a subscriber, this one cannot arrive early."""
    stocked(two_databases, on_hand=20)
    first = an_order(two_databases, customer="Ana")
    second = an_order(two_databases, customer="Bruno")

    assert deliver(two_databases) == 2

    invoiced = [
        one.order_id
        for one in two_databases.store("notifications").fetch_all(
            notifications.Tolds,
            # Read with an order, for the same reason the relay claims with one: without it the
            # engine hands the rows back however it likes, and the question was about order.
            Criteria(
                where=Condition(field="said", value=InvoiceIssued.name),
                order=(Sort(field="id"),),
            ),
        )
    ]
    assert invoiced == [first.order_id, second.order_id]


# --- 5. a saga across the boundary ------------------------------------------------------------


def test_a_failure_on_the_far_side_leaves_the_near_side_standing(two_databases: Shop):
    """`inventory` committed its reservation in one database. `billing` fails in another. There
    is no transaction over both — that is what the boundary means, and it is why a compensation
    has to exist."""
    shop = paired(billing_refuses=True)
    stocked(shop)

    with pytest.raises(billing.RefusedToInvoice):
        an_order(shop, quantity=3)

    assert inventory.on_hand(shop.store("inventory"), "MUG") == 7
    assert shop.store("billing").count(billing.Invoices).value == 0


def test_the_compensation_crosses_back_as_a_command(two_databases: Shop):
    """Undoing is another step, and it goes the way everything else goes across a boundary: a
    command the other context answers. The release is a fact of its own — the reservation
    really happened."""
    shop = paired(billing_refuses=True)
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
    said = [one.said for one in inventory.history_of(shop.store("inventory"), order_id)]
    assert said == [StockReserved.name, "inventory.v1.released"]


def test_a_rejection_inside_the_pair_needs_no_compensation_at_all(two_databases: Shop):
    """The cheaper failure: the step never happened. `inventory` says no, `orders` moves the
    order, and nothing crossed that has to be undone."""
    stocked(two_databases, on_hand=1)
    placed = an_order(two_databases, quantity=5)

    order = two_databases.store("orders").get(orders.Order, placed.order_id)
    assert order is not None and order.state == "rejected: out of stock"
    assert inventory.on_hand(two_databases.store("inventory"), "MUG") == 1
    assert two_databases.store("billing").count(billing.Invoices).value == 0
    said = [
        one.said
        for one in two_databases.store("notifications").fetch_all(notifications.Tolds)
    ]
    assert StockRejected.name in said
