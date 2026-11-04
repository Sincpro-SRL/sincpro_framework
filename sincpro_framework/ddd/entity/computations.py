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

        computations = Computations["Invoice"](
            lambda i: [
                Compute(i.subtotal, depends=i.lines, by=Invoice.subtotal_of),
                Compute(i.total, depends=[i.subtotal, i.discount], by=Invoice.total_of),
            ]
        )

The computation is the entity's own method; the framework only orders the computations by what
they depend on and runs the ones a change reaches (`recompute`). The lambda names fields through
the entity, as `presentation` does, answers one `Compute` alone or a list of them, and runs once when the class is described; a cycle or a
name that is not a field is refused there.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Compute:
    """One field computed from others: `Compute(i.total, depends=[i.subtotal, i.discount],
    by=Invoice.total_of)`.

    `depends` is one field alone or a list of them. `by` is a method of the entity (or any
    function of the record) answering the new value.
    """

    field: object
    depends: object
    by: Callable[..., object]


@dataclass(frozen=True)
class Computations[T]:
    """What the entity computes, listed once. Left alone, nothing is computed."""

    declared: Callable[[T], Compute | Sequence[Compute]] | None = None
