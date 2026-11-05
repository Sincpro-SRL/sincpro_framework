"""A predicate written as Python, read once from its syntax tree into the language.

The golden cases below are what each piece of Python becomes; the last ones prove that what the
expression answers is what the lambda would — over records with no empty values, where Python and
the language agree — and that what the language cannot say is refused, naming the line.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from functools import partial

import pytest

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria import (
    All,
    Any,
    Condition,
    FromField,
    Not,
    Operator,
    Through,
    Today,
)
from sincpro_framework.ddd.criteria.services.predicates import expression_of
from sincpro_framework.ddd.criteria.services.programs import compiled


class State(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    VOID = "void"


@dataclass
class Customer:
    city: str = ""


@dataclass
class Line:
    qty: int = 1


@dataclass
class Sale:
    state: State = State.DRAFT
    total: Decimal = Decimal("0")
    paid: Decimal = Decimal("0")
    delivery: str | None = None
    active: bool = True
    tags: list[str] = field(default_factory=list)
    customer: Customer = field(default_factory=Customer)
    lines: list[Line] = field(default_factory=list)


def eq(name, value):
    return Condition(field=name, value=value)


def op(name, operator, value):
    return Condition(field=name, operator=operator, value=value)


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        (lambda s: s.state == State.CONFIRMED, eq("state", State.CONFIRMED)),
        (
            lambda s: s.state == State.CONFIRMED and s.delivery is None,
            All(all=[eq("state", State.CONFIRMED), op("delivery", Operator.IS_NULL, True)]),
        ),
        (lambda s: s.delivery is not None, op("delivery", Operator.IS_NULL, False)),
        (
            lambda s: 0 <= s.total <= 1000,
            All(all=[op("total", Operator.GTE, 0), op("total", Operator.LTE, 1000)]),
        ),
        (lambda s: 100 < s.total, op("total", Operator.GT, 100)),
        (
            lambda s: s.state in [State.DRAFT, State.VOID],
            op("state", Operator.IN, [State.DRAFT, State.VOID]),
        ),
        (lambda s: s.state not in (State.VOID,), op("state", Operator.NOT_IN, [State.VOID])),
        (lambda s: "vip" in s.tags, op("tags", Operator.CONTAINS, "vip")),
        (lambda s: s.paid == s.total, op("paid", Operator.EQ, FromField(name="total"))),
        (lambda s: s.active, eq("active", True)),
        (lambda s: not s.active, Not(negate=eq("active", True))),
        (
            lambda s: s.total > 100 or s.state == State.VOID,
            Any(any=[op("total", Operator.GT, 100), eq("state", State.VOID)]),
        ),
        (
            lambda s: s.customer.city == "La Paz",
            Through(through="customer", selection=eq("city", "La Paz")),
        ),
        (
            lambda s: any(line.qty > 10 for line in s.lines),
            Through(through="lines", selection=op("qty", Operator.GT, 10)),
        ),
        (
            lambda s: all(line.qty > 0 for line in s.lines),
            Not(
                negate=Through(
                    through="lines", selection=Not(negate=op("qty", Operator.GT, 0))
                )
            ),
        ),
        (lambda s: s.total > Decimal("99.5"), op("total", Operator.GT, Decimal("99.5"))),
    ],
)
def test_each_piece_of_python_is_its_expression(predicate, expected):
    assert expression_of(Sale, predicate) == expected


def test_a_dynamic_value_is_kept_to_be_read_when_the_filter_runs():
    read = expression_of(Sale, lambda s: s.delivery != Today())

    assert read == op("delivery", Operator.NE, Today())


def test_two_lambdas_on_one_line_are_told_apart():
    first, second = (lambda s: s.total > 1), (lambda s: s.state == State.VOID)

    assert expression_of(Sale, first) == op("total", Operator.GT, 1)
    assert expression_of(Sale, second) == eq("state", State.VOID)


def test_a_name_the_lambda_closes_over_is_read_once():
    limit = Decimal("250")

    assert expression_of(Sale, lambda s: s.total >= limit) == op("total", Operator.GTE, limit)


@pytest.mark.parametrize(
    ("predicate", "refusal"),
    [
        (lambda s: s.total * 2 > 10, "compares no field of the record"),
        (lambda s: s.delivery.upper() == "X", "compares no field of the record"),
        (lambda s: s.active is True, "only `is None` is a question"),
        (lambda s: len(s.lines) > 2, "compares no field of the record"),
    ],
)
def test_what_the_language_cannot_say_is_refused_naming_the_line(predicate, refusal):
    with pytest.raises(exceptions.ProgrammingError, match=refusal) as refused:
        expression_of(Sale, predicate)

    assert "test_predicates.py:" in str(refused.value)


SALES = [
    Sale(
        State.CONFIRMED,
        Decimal("120"),
        Decimal("120"),
        "monday",
        True,
        ["vip"],
        Customer("La Paz"),
        [Line(12)],
    ),
    Sale(
        State.DRAFT,
        Decimal("80"),
        Decimal("10"),
        "tuesday",
        False,
        [],
        Customer("Sucre"),
        [Line(1), Line(2)],
    ),
    Sale(
        State.VOID,
        Decimal("5"),
        Decimal("5"),
        "friday",
        True,
        ["vip", "late"],
        Customer("La Paz"),
        [],
    ),
]
AGREEING = [
    lambda s: s.state == State.CONFIRMED and s.total > 100,
    lambda s: 10 <= s.total <= 100 or not s.active,
    lambda s: s.paid == s.total,
    lambda s: "vip" in s.tags and s.customer.city == "La Paz",
    lambda s: any(line.qty > 10 for line in s.lines),
    lambda s: all(line.qty > 0 for line in s.lines),
    lambda s: s.state not in [State.VOID],
]


@pytest.mark.parametrize("predicate", AGREEING)
def test_the_expression_answers_what_the_lambda_would(predicate):
    program = compiled(expression_of(Sale, predicate))

    assert [program(partial(getattr, one)) for one in SALES] == [
        predicate(one) for one in SALES
    ]
