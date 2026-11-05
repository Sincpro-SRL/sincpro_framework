"""What a `@compute` returns, written down when the language can say it — and only then: the
formula answers what the method answers, and a body it cannot say has no formula.
"""

from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity, compute, recompute
from sincpro_framework.ddd.criteria import Formulas
from sincpro_framework.ddd.criteria.services.programs import reader
from sincpro_framework.ddd.metamodel import contract_of, model_of

PRICES = {"desk": Decimal("100")}


def qty_times_price(line):
    return line.qty * line.price


def field_of(name):
    return {"$": "field", "name": name}


@dataclass
class Line:
    product: str = ""
    qty: int = 1
    price: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    listed: Decimal = Decimal("0")

    @compute(lambda l: l.amount, depends=lambda l: [l.qty, l.price])
    def amount_of(self) -> Decimal:
        """What the line costs."""
        return self.qty * self.price

    @compute(lambda l: l.listed, depends=lambda l: l.product)
    def listed_of(self) -> Decimal:
        return PRICES.get(self.product, Decimal("0"))


@dataclass
class Sale(Entity):
    lines: list[Line] = field(default_factory=list)
    discount: Decimal = Decimal("0")
    subtotal: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    share: Decimal = Decimal("0")

    @compute(lambda s: s.subtotal, depends=lambda s: s.lines)
    def subtotal_of(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0"))

    @compute(lambda s: s.total, depends=lambda s: [s.subtotal, s.discount])
    def total_of(self) -> Decimal:
        return self.subtotal - self.discount * Decimal("1.13") + -self.discount

    @compute(lambda s: s.share, depends=lambda s: s.total)
    def share_of(self) -> Decimal:
        return self.total / 3


def test_each_body_the_language_says_is_its_formula():
    assert Formulas.of_a_method(Line.amount_of) == {
        "op": "*",
        "args": [field_of("qty"), field_of("price")],
    }
    assert Formulas.of_a_method(Sale.subtotal_of) == {
        "op": "sum",
        "of": "lines",
        "each": field_of("amount"),
        "from": "0",
    }
    assert Formulas.of_a_method(Sale.total_of) == {
        "op": "+",
        "args": [
            {
                "op": "-",
                "args": [
                    field_of("subtotal"),
                    {"op": "*", "args": [field_of("discount"), "1.13"]},
                ],
            },
            {"op": "-", "args": [field_of("discount")]},
        ],
    }


@pytest.mark.parametrize(
    "method",
    [Line.listed_of, Sale.share_of, qty_times_price.__call__],
    ids=["a read of something else", "a division", "no source at hand"],
)
def test_a_body_the_language_does_not_say_has_no_formula(method):
    assert Formulas.of_a_method(method) is None


def test_the_formula_answers_what_the_method_answers():
    sale = Sale(
        lines=[Line("desk", 2, Decimal("12.50")), Line("chair", 3, Decimal("0.10"))],
        discount=Decimal("1.5"),
    )
    recompute(sale)
    formulas = model_of(Sale).formulas
    line_formula = model_of(Line).formulas["amount"]

    assert [Formulas.value_of(line_formula, reader(one)) for one in sale.lines] == [
        one.amount for one in sale.lines
    ]
    assert Formulas.value_of(formulas["subtotal"], reader(sale)) == sale.subtotal
    assert Formulas.value_of(formulas["total"], reader(sale)) == sale.total
    assert "share" not in formulas


def test_a_client_is_published_the_formula_of_each_field_it_can_compute():
    contract = contract_of(Sale)
    pointed = contract["properties"]["lines"]["items"]["$ref"]  # the line, named once
    line = contract["$defs"][pointed.rsplit("/", 1)[-1]]["properties"]

    assert line["amount"]["x-sincpro"]["computes"]["op"] == "*"
    assert "computes" not in line["listed"]["x-sincpro"]
    assert MemoryRepository().definition(Sale).fields["subtotal"].computes is not None
