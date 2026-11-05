"""A computation simple enough to be written down: what a `@compute` method returns, read from its
syntax tree into a formula of the language, which a client evaluates the same way.

    @compute(lambda l: l.amount, depends=lambda l: [l.qty, l.price])
    def amount_of(self) -> Decimal:
        return self.qty * self.price
        ->  {"op": "*", "args": [{"$": "field", "name": "qty"}, {"$": "field", "name": "price"}]}

    @compute(lambda s: s.total, depends=lambda s: s.lines)
    def total_of(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0"))
        ->  {"op": "sum", "of": "lines", "each": {"$": "field", "name": "amount"}, "from": "0"}

Context: only what is exact in every language is written down — `+`, `-`, `*` and a sum over the
records a field holds, over the record's own fields and literal numbers. A body that does anything
else — a division, a condition, a call, a read of another service — has no formula: the server
computes it, and a form asks it in a preview, as it always did.

A formula is JSON:

    {"$": "field", "name": "qty"}                    a field of the record
    12, "12.50"                                      a literal; a text is an exact decimal
    {"op": "+" | "-" | "*", "args": [a, b, …]}       left to right; "-" with one argument negates
    {"op": "sum", "of": "lines", "each": formula,    each record `lines` holds, added up from
     "from": 0 | "0"}                                zero — a decimal one when the body says so
"""

import ast
import inspect
import textwrap
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sincpro_framework.ddd.criteria.services.programs import held, reader

OPERATORS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}
"""The arithmetic a formula writes down: exact over decimals, the same in every language."""


def _field(name: str) -> dict[str, Any]:
    """A field of the record, as the language names it — the same shape as `FromField`."""
    return {"$": "field", "name": name}


def _read(node: ast.expr, itself: str) -> Any:
    """The formula `node` writes over the record named `itself`; `None` when it writes anything
    a formula does not say.

    1. A literal number; `Decimal("12.50")` as its text.
    2. `itself.name` — a field of the record.
    3. `a + b`, `a - b`, `a * b`, `-a`.
    4. `sum(each for one in itself.lines)`, from zero — `0`, or `Decimal("0")` written as `"0"`
       — each line read with `one` as itself.
    Final: anything else — `None`.
    """
    match node:
        case ast.Constant(value=value) if isinstance(value, int | float) and not isinstance(
            value, bool
        ):
            return value
        case ast.Call(func=ast.Name(id="Decimal"), args=[ast.Constant(value=str() | int())]):
            return str(Decimal(str(node.args[0].value)))  # type: ignore[attr-defined]
        case ast.Attribute(value=ast.Name(id=name), attr=attr) if name == itself:
            return _field(attr)
        case ast.UnaryOp(op=ast.USub(), operand=operand):
            inner = _read(operand, itself)
            return None if inner is None else {"op": "-", "args": [inner]}
        case ast.BinOp(left=left, op=operator, right=right) if type(operator) in OPERATORS:
            parts = [_read(left, itself), _read(right, itself)]
            if any(part is None for part in parts):
                return None
            return {"op": OPERATORS[type(operator)], "args": parts}
        case ast.Call(func=ast.Name(id="sum"), args=[ast.GeneratorExp() as walked, *start]):
            begun = _read(start[0], itself) if start else 0
            if begun not in (0, "0"):
                return None
            match walked.generators:
                case [
                    ast.comprehension(
                        target=ast.Name(id=one),
                        iter=ast.Attribute(value=ast.Name(id=name), attr=relation),
                        ifs=[],
                    )
                ] if (
                    name == itself
                ):
                    each = _read(walked.elt, one)
                    if each is None:
                        return None
                    return {"op": "sum", "of": relation, "each": each, "from": begun}
    return None


def formula_of(method: Callable[..., Any]) -> Any:
    """The formula `method` returns, when its body is one `return` the language writes down;
    `None` otherwise — a body with more than a return, one whose source is not at hand, or a
    return a formula does not say.

        formula_of(Line.amount_of)    ->  {"op": "*", "args": [qty, price]}
        formula_of(Sale.discount_of)  ->  None     # it reads a price list
    """
    try:
        source = textwrap.dedent(inspect.getsource(method))
    except (OSError, TypeError):
        return None
    tree = ast.parse(source)
    defined = tree.body[0] if tree.body else None
    if not isinstance(defined, ast.FunctionDef) or not defined.args.args:
        return None
    body = [
        one
        for one in defined.body
        if not (isinstance(one, ast.Expr) and isinstance(one.value, ast.Constant))
    ]
    match body:
        case [ast.Return(value=returned)] if returned is not None:
            return _read(returned, defined.args.args[0].arg)
    return None


def _number(value: Any) -> Any:
    """A value as arithmetic takes it: a text is an exact decimal, a number stays itself."""
    return Decimal(value) if isinstance(value, str) else value


def value_of(formula: Any, read: Callable[[str], Any]) -> Any:
    """What `formula` answers over the record whose fields `read` gives — what the server's
    computation returns for a body written down, the reference every client is held to.

        value_of({"op": "*", "args": [qty, price]}, {"qty": 2, "price": Decimal("12.50")}.get)
            ->  Decimal("25.00")
    """
    if isinstance(formula, dict) and formula.get("$") == "field":
        return _number(read(formula["name"]))
    if not isinstance(formula, dict):
        return _number(formula)
    if formula["op"] == "sum":
        return sum(
            (value_of(formula["each"], reader(one)) for one in held(read(formula["of"]))),
            _number(formula["from"]),
        )
    values = [value_of(part, read) for part in formula["args"]]
    if formula["op"] == "-" and len(values) == 1:
        return -values[0]
    answered = values[0]
    for one in values[1:]:
        answered = {
            "+": answered + one,
            "-": answered - one,
            "*": answered * one,
        }[formula["op"]]
    return answered
