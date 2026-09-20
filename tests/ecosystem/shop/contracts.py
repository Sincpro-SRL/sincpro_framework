"""The facts every context may hear, declared once where all of them can see them.

A shop: somebody places an order, the stock is reserved, an invoice is issued, and the customer
is told. Four bounded contexts, and these are the four things they say to each other.

**One class, several handlers.** A context that reacts to a fact registers a Feature for this
class on its own bus; the subscriber runs each bus that knows the name. Declaring the shape a
second time per listener also works — the subscriber rebuilds the event as the class each bus
registered — but it drifts, and nothing here needs it.

These carry no behaviour and no table. **Where a fact is kept is each context's own decision**,
and much of what this package demonstrates is two contexts keeping the same fact in two places,
both correctly.
"""

from dataclasses import dataclass

from sincpro_framework.ddd.events import DomainEvent


@dataclass(kw_only=True)
class OrderPlaced(DomainEvent):
    name = "orders.v1.placed"
    order_id: str = ""
    sku: str = ""
    quantity: int = 0
    total: int = 0


@dataclass(kw_only=True)
class StockReserved(DomainEvent):
    name = "inventory.v1.reserved"
    order_id: str = ""
    sku: str = ""
    quantity: int = 0


@dataclass(kw_only=True)
class StockRejected(DomainEvent):
    name = "inventory.v1.rejected"
    order_id: str = ""
    sku: str = ""
    wanted: int = 0
    left: int = 0


@dataclass(kw_only=True)
class InvoiceIssued(DomainEvent):
    name = "billing.v1.invoice_issued"
    order_id: str = ""
    invoice_id: str = ""
    total: int = 0
