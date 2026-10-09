"""The fields an entity computes from others, declared once so a preview and a save compute
them the same way.

    @dataclass
    class Invoice(Entity):
        lines: list[InvoiceLine] = field(default_factory=list)
        discount: Decimal = Decimal("0")
        subtotal: Decimal = Decimal("0")
        total: Decimal = Decimal("0")

        def subtotal_of(self) -> Decimal:
            return sum((line.qty * line.price for line in self.lines), Decimal("0"))

        def total_of(self) -> Decimal:
            return self.subtotal - self.discount

        derivations = Derivations["Invoice"](
            lambda i: [
                Derive(i.subtotal, depends=i.lines, by=Invoice.subtotal_of),
                Derive(i.total, depends=[i.subtotal, i.discount], by=Invoice.total_of),
            ]
        )

The computation is the entity's own method; the framework only orders the derivations by what
they depend on and runs the ones a change reaches (`recompute`). The lambda names fields through
the entity, as `presentation` does, answers one `Derive` alone or a list of them, and runs once when the class is described; a cycle or a
name that is not a field is refused there.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Derive:
    """One field computed from others: `Derive(i.total, depends=[i.subtotal, i.discount],
    by=Invoice.total_of)`.

    `depends` is one field alone or a list of them. `by` is a method of the entity (or any
    function of the record) answering the new value.
    """

    field: object
    depends: object
    by: Callable[..., object]


@dataclass(frozen=True)
class Derivations[T]:
    """What the entity computes, listed once. Left alone, nothing is computed."""

    declared: Callable[[T], Derive | Sequence[Derive]] | None = None
