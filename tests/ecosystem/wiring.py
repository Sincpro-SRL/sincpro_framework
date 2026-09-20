"""The composition root: the only module that knows all four contexts.

**The same four contexts, wired three ways.** Nothing under `shop/` knows which of these it is
running under — that is the claim `docs/shapes.md` makes, and this is where it is measured.

    monolith()      one database. Every table in it, foreign keys available anywhere.
    paired()        two databases. orders+inventory share one, billing+notifications the other.
    distributed()   four databases, one each.

What differs is **which engine a repository points at**, and nothing else. The aggregates, the
tables, the mappings, the events and the buses are identical in all three.

**Nothing imports backwards.** A context receives its repository and, when it publishes, its
publisher; it never imports the queue, and the queue never imports a context. That is why there
is no late import anywhere in this package and no `# noqa: E402`. The knot it cuts: a queue
needs a subscriber, a subscriber needs the buses, and a bus needs the publisher the queue is
behind — `SyncQueue` takes a *function*, so the queue is built before a single bus exists.
"""

from dataclasses import dataclass, field

from sincpro_framework import UseFramework
from sincpro_framework.events import Publisher, Subscriber, SyncQueue
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .shop import billing, inventory, notifications, orders

CONTEXTS = {
    "orders": orders,
    "inventory": inventory,
    "billing": billing,
    "notifications": notifications,
}


@dataclass
class Shop:
    """Four bounded contexts and however many databases the shape asked for."""

    shape: str
    buses: dict[str, UseFramework] = field(default_factory=dict)
    stores: dict[str, Repository] = field(default_factory=dict)
    databases: dict[str, Database] = field(default_factory=dict)
    publisher: Publisher = field(init=False)

    def __getitem__(self, name: str) -> UseFramework:
        return self.buses[name]

    def store(self, name: str) -> Repository:
        return self.stores[name]

    def shares_a_database(self, one: str, another: str) -> bool:
        """Whether these two contexts could be joined by a foreign key at all."""
        return self.databases[one] is self.databases[another]


def _built(
    shape: str, per_context: dict[str, Database], billing_refuses: bool = False
) -> Shop:
    """1. Create every context's tables on the database it was given.
    2. The queue, before any bus exists, over a function that will find them.
    3. Each context, handed what it needs and importing nothing upward.
    4. Final: the buses registered, so the first publish finds all four.
    """
    shop = Shop(shape=shape, databases=per_context)

    for name, module in CONTEXTS.items():
        module.metadata.create_all(per_context[name].engine)
        shop.stores[name] = Repository(per_context[name])

    shop.publisher = Publisher(SyncQueue(lambda: Subscriber(*shop.buses.values())))

    def ask_orders(order_id: str) -> orders.ResponseAboutOrder:
        """How `billing` reaches `orders`: a command on that bus, not an import and not a
        join. Handed in, so `billing` never learns `orders` exists as a module."""
        return shop.buses["orders"](
            orders.CommandTellAboutOrder(order_id=order_id), orders.ResponseAboutOrder
        )

    shop.buses["orders"] = orders.build(shop.stores["orders"], shop.publisher)
    shop.buses["inventory"] = inventory.build(shop.stores["inventory"], shop.publisher)
    shop.buses["billing"] = billing.build(
        shop.stores["billing"], ask_orders, refuse=billing_refuses
    )
    shop.buses["notifications"] = notifications.build(shop.stores["notifications"])
    return shop


def monolith(billing_refuses: bool = False) -> Shop:
    """**One database, everything in it.** Every table is reachable from every other, so a
    reference between two contexts *can* be a foreign key the engine enforces, and a report
    across them is one query. What you give up is deploying or scaling one context without the
    rest — and the discipline that keeps them out of each other's tables is now yours to
    enforce rather than the schema's.
    """
    one = Database("sqlite://")
    return _built("monolith", {name: one for name in CONTEXTS}, billing_refuses)


def paired(billing_refuses: bool = False) -> Shop:
    """**Two databases, two contexts each** — the shape a system actually reaches as it grows.

        orders + inventory        share one
        billing + notifications   share the other

    Inside a pair a reference can be a foreign key. Across the pairs it cannot, so it goes
    through the bus — a command the other context answers — or as a fact on the queue. Both
    kinds exist here at once, which is the point: the same system is transactional in one place
    and eventually consistent in another, and the domain code does not change between them.
    """
    first, second = Database("sqlite://"), Database("sqlite://")
    return _built(
        "paired",
        {
            "orders": first,
            "inventory": first,
            "billing": second,
            "notifications": second,
        },
        billing_refuses,
    )


def distributed(billing_refuses: bool = False) -> Shop:
    """**A database each.** No reference between contexts can be a key, so every one of them is
    a fact that crossed — which is why the outbox stops being optional here.
    """
    return _built(
        "distributed", {name: Database("sqlite://") for name in CONTEXTS}, billing_refuses
    )


SHAPES = {"monolith": monolith, "paired": paired, "distributed": distributed}
