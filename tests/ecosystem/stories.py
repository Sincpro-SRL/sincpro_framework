"""What every case does to the shop, so the three cases differ in what they *assert*, not in
what they run.

A test that builds its own scenario inline proves that scenario. These are here so the three
shapes are compared on the same one.
"""

from dataclasses import dataclass

from .shop import billing, inventory, notifications, orders
from .wiring import Shop


@dataclass
class Placed:
    order_id: str
    sku: str
    quantity: int
    total: int


def stocked(shop: Shop, sku: str = "MUG", on_hand: int = 10) -> None:
    shop["inventory"](
        inventory.CommandStock(sku=sku, on_hand=on_hand), inventory.ResponseStock
    )


def an_order(
    shop: Shop,
    customer: str = "Ana",
    sku: str = "MUG",
    quantity: int = 3,
    price: int = 50,
) -> Placed:
    """Place one order and let everything it sets off run, except the relay."""
    answered = shop["orders"](
        orders.CommandPlaceOrder(customer=customer, sku=sku, quantity=quantity, price=price),
        orders.ResponseOrder,
    )
    return Placed(
        order_id=answered.order_id, sku=sku, quantity=quantity, total=quantity * price
    )


def deliver(shop: Shop) -> int:
    """Run billing's relay: what crosses the boundary, and when."""
    return billing.relay(shop.store("billing"), shop.publisher)


def timeline(shop: Shop, order_id: str) -> notifications.Timeline:
    return notifications.timeline_of(shop.store("notifications"), order_id)
