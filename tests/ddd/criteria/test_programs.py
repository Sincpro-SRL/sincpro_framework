"""A filter compiled once into the function that answers it, answering exactly as walking the tree
does — the same comparisons, the same NULL rules, the same crossing of a relation — and kept, so a
page of a thousand records compiles it once."""

from dataclasses import dataclass, field
from decimal import Decimal
from functools import partial

from sincpro_framework.ddd.criteria import (
    All,
    Any,
    Condition,
    FromField,
    Not,
    Operator,
    Through,
)
from sincpro_framework.ddd.criteria.services import programs
from sincpro_framework.ddd.criteria.services.comparison import comparator, compare
from sincpro_framework.ddd.criteria.services.programs import KEPT, compiled, program_of


@dataclass
class Line:
    qty: int
    price: Decimal | None = None


@dataclass
class Order:
    state: str
    total: Decimal
    paid: Decimal | None = None
    lines: list[Line] = field(default_factory=list)


ORDERS = [
    Order("posted", Decimal("120"), Decimal("120"), [Line(12, Decimal("10"))]),
    Order("posted", Decimal("80"), None, [Line(1), Line(2, Decimal("40"))]),
    Order("draft", Decimal("500"), Decimal("100"), []),
    Order("void", Decimal("0"), None, [Line(30)]),
]
FILTERS = [
    Condition(field="state", value="posted"),
    All(
        all=[
            Condition(field="state", value="posted"),
            Condition(field="total", operator=Operator.GT, value=100),
        ]
    ),
    Any(
        any=[
            Condition(field="paid", operator=Operator.IS_NULL, value=True),
            Condition(field="total", operator=Operator.LT, value=10),
        ]
    ),
    Not(negate=Condition(field="paid", operator=Operator.EQ, value=Decimal("120"))),
    Condition(field="paid", operator=Operator.EQ, value=FromField(name="total")),
    Through(
        through="lines", selection=Condition(field="qty", operator=Operator.GT, value=10)
    ),
    Not(
        negate=Through(
            through="lines",
            selection=Condition(field="price", operator=Operator.IS_NULL, value=True),
        )
    ),
    None,
]


def _walk(expression, record) -> bool:
    """What the tree answers, walked node by node: the reference a program must agree with."""
    match expression:
        case None:
            return True
        case Condition():
            value = expression.value
            if isinstance(value, FromField):
                value = getattr(record, value.name)
                if value is None:
                    return False
            return compare(getattr(record, expression.field), expression.operator, value)
        case All():
            return all(_walk(one, record) for one in expression.all)
        case Any():
            return any(_walk(one, record) for one in expression.any)
        case Not():
            return not _walk(expression.negate, record)
        case Through():
            return any(
                _walk(expression.selection, one)
                for one in getattr(record, expression.through)
            )
    raise AssertionError(f"not a filter: {expression!r}")


def test_a_program_answers_every_record_as_walking_the_tree_does():
    for expression in FILTERS:
        program = compiled(expression)

        assert [program(partial(getattr, one)) for one in ORDERS] == [
            _walk(expression, one) for one in ORDERS
        ], expression


def test_a_program_is_compiled_once_and_kept():
    asked = Condition(field="state", value="posted")

    assert program_of(asked) is program_of(Condition(field="state", value="posted"))


def test_a_decimal_and_a_text_are_two_programs():
    assert program_of(Condition(field="total", value=Decimal("1"))) is not program_of(
        Condition(field="total", value="1")
    )


def test_the_programs_kept_are_bounded():
    for number in range(KEPT + 10):
        program_of(Condition(field="n", value=number))

    assert len(programs._programs) <= KEPT


ACTUALS = [None, 0, 1, 5, Decimal("5"), "Sincpro SRL", "pro", [], ["a", "b"], True, False]
CASES = [
    (Operator.EQ, 5),
    (Operator.EQ, []),
    (Operator.NE, 5),
    (Operator.NE, []),
    (Operator.GT, 1),
    (Operator.GTE, 1),
    (Operator.LT, 5),
    (Operator.LTE, 5),
    (Operator.IN, [1, 5]),
    (Operator.NOT_IN, [1, 5]),
    (Operator.BETWEEN, [1, 5]),
    (Operator.LIKE, "PRO"),
    (Operator.STARTS_WITH, "sin"),
    (Operator.CONTAINS, "a"),
    (Operator.NOT_CONTAINS, "a"),
    (Operator.IS_NULL, True),
    (Operator.IS_NULL, False),
]


def answered(function, *arguments):
    """What a comparison answers, a refusal of the types being an answer too."""
    try:
        return function(*arguments)
    except TypeError:
        return TypeError


def test_a_comparator_answers_every_operator_as_compare_does():
    for operator, value in CASES:
        decided = comparator(operator, value)
        for actual in ACTUALS:
            assert answered(decided, actual) == answered(compare, actual, operator, value), (
                operator,
                value,
                actual,
            )
