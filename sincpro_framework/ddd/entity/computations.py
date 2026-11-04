"""The fields an entity computes from others, declared on the method that computes each one, so a
preview and a save compute them the same way.

    @dataclass
    class Invoice(Entity):
        partner_id: str | None = None
        payment_term: str | None = None
        lines: list[InvoiceLine] = field(default_factory=list)
        discount: Decimal = Decimal("0")
        subtotal: Decimal = Decimal("0")
        total: Decimal = Decimal("0")

        @compute(lambda i: i.subtotal, depends=lambda i: i.lines)
        def subtotal_of(self) -> Decimal:
            return sum((line.qty * line.price for line in self.lines), Decimal("0"))

        @compute(lambda i: i.total, depends=lambda i: [i.subtotal, i.discount])
        def total_of(self) -> Decimal:
            return self.subtotal - self.discount

        @compute(lambda i: i.payment_term, depends=lambda i: i.partner_id, editable=True)
        def payment_term_of(self) -> str | None:
            return terms_of(self.partner_id)

The method is the entity's own; the decorator only says which field it computes and what it
reads. The framework orders the computations by what they depend on and runs the ones a change
reaches (`recompute`). Each lambda names fields through the entity and runs once, when the class
is described, against a stand-in whose attributes are the field names: a cycle, a field computed
twice or a name that is not a field is refused there, naming it.

`editable=True` is a suggestion, not a rule: it runs only when a change reaches what it depends
on — the partner picked, the payment term proposed — and never when a save computes the record
whole, so a value the user typed over it is the one stored. A screen shows it editable.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

COMPUTE = "__sincpro_compute__"
"""The attribute under which a method carries its `ComputeDeclaration`."""


@dataclass(frozen=True)
class ComputeDeclaration:
    """What `@compute` left on a method: the lambdas as written, read when the class is described."""

    field: Callable[[Any], object]
    depends: Callable[[Any], object] | None
    editable: bool


def compute[F: Callable[..., Any]](
    field: Callable[[Any], object],
    depends: Callable[[Any], object] | None = None,
    editable: bool = False,
) -> Callable[[F], F]:
    """The method computes `field` from `depends`: `@compute(lambda i: i.total,
    depends=lambda i: [i.subtotal, i.discount])`.

    `field` names one field; `depends` one field alone or a list of them — none when the value
    reads nothing a form changes, so only a save or a whole preview computes it. `editable`
    computes it only when a change reaches `depends`. The method answers the new value.
    """

    def declared(method: F) -> F:
        setattr(method, COMPUTE, ComputeDeclaration(field, depends, editable))
        return method

    return declared
