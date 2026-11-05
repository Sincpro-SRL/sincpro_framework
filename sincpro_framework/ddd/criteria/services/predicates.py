"""A predicate written as Python, read as an expression of the language.

    expression_of(Sale, lambda s: s.state == SaleState.CONFIRMED and s.delivery is None)
        ->  All([state = "confirmed", delivery is null])
    expression_of(Sale, lambda s: any(line.qty > 10 for line in s.lines))
        ->  Through("lines", qty > 10)

The lambda is typed by the entity, so a type checker refuses a misspelled field and a value of the
wrong type; it is never run to answer a record. Its syntax tree is read once — the shape C#'s LINQ
gives with expression trees, and Pony ORM and SQLAlchemy's `lambda_stmt` read in Python — and what
answers a record is the compiled program of the expression, so Python, SQL and the browser share
one semantics (docs/spec/model.md §15).

What the language says is a subset of Python, refused loudly outside it, naming the line:

    s.total > 100, 0 <= s.total <= 1000      comparisons, chained
    s.state in [A, B], s.state not in (…)    membership in a list of values
    s.delivery is None, … is not None        empty or not
    s.paid == s.total                        a field against another of the same record
    s.active, not s.archived                 a boolean field alone
    a and b, a or b, not a                   all, any, not
    s.customer.city == "La Paz"              through a relation to one
    any(l.qty > 10 for l in s.lines)         some record of a relation answers
    all(l.qty > 0 for l in s.lines)          every record of a relation answers

Anything that does not read the lambda's own parameter — `SaleState.DRAFT`, `Decimal("1")`,
`Today()` — is read once, when the lambda is compiled: a value that changes per call is a dynamic
value of the language (`Today()`, `Now()`, `FromContext(…)`), resolved when the filter runs.
"""

import ast
import inspect
import linecache
from collections.abc import Callable
from typing import Any

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria.domain.expression import (
    All,
    Any_,
    Condition,
    Expression,
    Not,
    Operator,
    Through,
)
from sincpro_framework.ddd.criteria.domain.values import FromField

COMPARED = {
    ast.Eq: Operator.EQ,
    ast.NotEq: Operator.NE,
    ast.Gt: Operator.GT,
    ast.GtE: Operator.GTE,
    ast.Lt: Operator.LT,
    ast.LtE: Operator.LTE,
    ast.In: Operator.IN,
    ast.NotIn: Operator.NOT_IN,
}
TURNED = {
    Operator.GT: Operator.LT,
    Operator.GTE: Operator.LTE,
    Operator.LT: Operator.GT,
    Operator.LTE: Operator.GTE,
}
"""What an operator becomes when its sides are swapped: `100 < s.total` is `s.total > 100`."""


def lambda_of(function: Callable[..., Any]) -> tuple[ast.Lambda, str]:
    """The syntax tree of `function`'s own lambda, and where it is written (`file:line`).

    1. The whole source file is parsed — a lambda inside a decorator or a call spanning lines has
       no source of its own that parses alone.
    2. Of the lambdas written on its first line, the one whose code is `function`'s, compared
       instruction by instruction: two lambdas on one line are told apart.
       2.1 A lambda that closes over a name compiles differently alone; it is the one whose body
           starts where `function`'s first instruction does.
    Final: refused when the source is not there (code built at runtime registers it in
    `linecache` first) or no lambda matches.
    """
    code = function.__code__
    where = f"{code.co_filename}:{code.co_firstlineno}"
    lines = linecache.getlines(code.co_filename)
    if not lines:
        raise exceptions.ProgrammingError(
            f"{where}: the source of this lambda is not there to read; code built at runtime "
            "registers its source in linecache before its lambdas are compiled"
        )
    tree = ast.parse("".join(lines), code.co_filename)
    written = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Lambda) and node.lineno == code.co_firstlineno
    ]
    for node in written:
        built = compile(ast.Expression(body=node), code.co_filename, "eval")
        own = next(one for one in built.co_consts if inspect.iscode(one))
        if own.co_code == code.co_code and own.co_names == code.co_names:
            return node, where
    starts = [
        column
        for line, _, column, _ in code.co_positions()
        if line == code.co_firstlineno and column
    ]
    for node in written:
        if (
            starts
            and node.body.lineno == code.co_firstlineno
            and (node.body.col_offset == min(starts))
        ):
            return node, where
    raise exceptions.ProgrammingError(f"{where}: no lambda here matches the function given")


class _Reading:
    """One lambda being read: its parameter, the scope its constants are read from, and where it is
    written, for every refusal to name."""

    def __init__(
        self,
        function: Callable[..., Any],
        node: ast.Lambda,
        where: str,
        itself: str | None = None,
    ) -> None:
        """`function` gives the scope — its globals, the names it closed over, the builtins.
        `itself` names the parameter read alone — a value object's own value — as a field."""
        closure = inspect.getclosurevars(function)
        self.scope = {**closure.builtins, **closure.globals, **closure.nonlocals}
        self.root = node.args.args[0].arg
        self.where = where
        self.itself = itself

    def refused(self, node: ast.AST, why: str) -> exceptions.ProgrammingError:
        """The refusal of `node`, naming the line and the construct."""
        line = getattr(node, "lineno", "?")
        return exceptions.ProgrammingError(
            f"{self.where.rsplit(':', 1)[0]}:{line}: `{ast.unparse(node)}` {why}"
        )

    def reads_a_record(self, node: ast.AST, roots: frozenset[str]) -> bool:
        """Whether `node` reads one of the records being asked about — else it is a constant."""
        return any(isinstance(one, ast.Name) and one.id in roots for one in ast.walk(node))

    def constant(self, node: ast.expr) -> Any:
        """A part that reads no record, read once now from the lambda's scope."""
        try:
            return eval(
                compile(ast.Expression(body=node), self.where, "eval"), dict(self.scope)
            )
        except Exception as error:  # what the constant raised is the author's to see
            raise self.refused(node, f"could not be read as a value: {error}") from error

    def path(self, node: ast.expr, roots: frozenset[str]) -> list[str] | None:
        """The fields `node` reads from a record, outermost first: `s.customer.city` is
        `["customer", "city"]`; `None` when it is not a field of a record."""
        names: list[str] = []
        while isinstance(node, ast.Attribute):
            names.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name) and node.id in roots and names:
            return list(reversed(names))
        if isinstance(node, ast.Name) and node.id == self.root and self.itself and not names:
            return [self.itself]
        return None

    def through(self, path: list[str], inner: Expression) -> Expression:
        """`inner`, asked of the record at the end of `path`: every step but the last crosses a
        relation."""
        for relation in reversed(path[:-1]):
            inner = Through(through=relation, selection=inner)
        return inner

    def compared(
        self, left: ast.expr, operator: ast.cmpop, right: ast.expr, roots: frozenset[str]
    ) -> Expression:
        """One comparison: a field against a value, a value against a field (turned around), a
        field against another of the same record, or emptiness."""
        whole = ast.Compare(left=left, ops=[operator], comparators=[right])
        if isinstance(operator, (ast.Is, ast.IsNot)):
            field = self.path(left, roots)
            if field is None or not (isinstance(right, ast.Constant) and right.value is None):
                raise self.refused(
                    whole, "compares by identity: only `is None` is a question"
                )
            empty = Condition(
                field=field[-1], operator=Operator.IS_NULL, value=isinstance(operator, ast.Is)
            )
            return self.through(field, empty)
        kind = COMPARED.get(type(operator))
        if kind is None:
            raise self.refused(whole, "is not a comparison of the language")
        field, other = self.path(left, roots), self.path(right, roots)
        if field is not None and other is not None:
            if field[:-1] != other[:-1]:
                raise self.refused(whole, "compares fields of two different records")
            against = Condition(
                field=field[-1], operator=kind, value=FromField(name=other[-1])
            )
            return self.through(field, against)
        if field is not None and not self.reads_a_record(right, roots):
            value = self.constant(right)
            if kind in (Operator.IN, Operator.NOT_IN) and isinstance(
                value, (tuple, set, frozenset)
            ):
                value = list(value)
            return self.through(field, Condition(field=field[-1], operator=kind, value=value))
        if other is not None and not self.reads_a_record(left, roots):
            value = self.constant(left)
            if kind is Operator.IN:
                return self.through(
                    other, Condition(field=other[-1], operator=Operator.CONTAINS, value=value)
                )
            if kind is Operator.NOT_IN:
                return self.through(
                    other,
                    Condition(field=other[-1], operator=Operator.NOT_CONTAINS, value=value),
                )
            turned = TURNED.get(kind, kind)
            return self.through(
                other, Condition(field=other[-1], operator=turned, value=value)
            )
        raise self.refused(whole, "compares no field of the record with a value")

    def crossing(self, node: ast.Call, roots: frozenset[str], every: bool) -> Expression:
        """`any(… for one in s.lines)` — some record of the relation answers — or `all(…)`, every
        record does: none answers the opposite."""
        generator = node.args[0] if len(node.args) == 1 else None
        if not isinstance(generator, ast.GeneratorExp) or len(generator.generators) != 1:
            raise self.refused(node, "is not `any(… for one in s.relation)`")
        loop = generator.generators[0]
        relation = self.path(loop.iter, roots)
        if relation is None or not isinstance(loop.target, ast.Name) or loop.ifs:
            raise self.refused(node, "does not run over a relation of the record")
        inner = self.read(generator.elt, frozenset({loop.target.id}))
        crossed: Expression = Not(negate=inner) if every else inner
        for relation_name in reversed(relation):
            crossed = Through(through=relation_name, selection=crossed)
        return Not(negate=crossed) if every else crossed

    def read(self, node: ast.expr, roots: frozenset[str]) -> Expression:
        """The expression `node` says about the records `roots` name.

        1. `and` / `or`: every part read, as an `All` / an `Any`.
        2. `not`: the part read, negated.
        3. A comparison: each pair of neighbours read (`compared`), a chain as an `All`.
        4. `any(…)` / `all(…)` over a relation: a crossing (`crossing`).
        5. A field alone: a boolean field that holds.
        Final: anything else is refused, naming the line.
        """
        if isinstance(node, ast.BoolOp):
            parts = [self.read(one, roots) for one in node.values]
            return All(all=parts) if isinstance(node.op, ast.And) else Any_(any=parts)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return Not(negate=self.read(node.operand, roots))
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            parts = [
                self.compared(sides[at], operator, sides[at + 1], roots)
                for at, operator in enumerate(node.ops)
            ]
            return parts[0] if len(parts) == 1 else All(all=parts)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("any", "all")
        ):
            return self.crossing(node, roots, every=node.func.id == "all")
        field = self.path(node, roots)
        if field is not None:
            return self.through(field, Condition(field=field[-1], value=True))
        raise self.refused(node, "is not an expression of the language")


def expression_of[T](entity: type[T], predicate: Callable[[T], bool]) -> Expression:
    """The expression `predicate` says about a record of `entity`, read from its syntax tree.

        expression_of(Sale, lambda s: s.total > 100 and s.state != SaleState.VOID)
            ->  All([total > 100, state != "void"])

    `entity` types the lambda's parameter, so a type checker reads each field against it; the
    fields themselves are checked against the entity's model wherever the expression is used.
    """
    node, where = lambda_of(predicate)
    reading = _Reading(predicate, node, where)
    return reading.read(node.body, frozenset({reading.root}))


def value_expression_of(
    predicate: Callable[[Any], bool], itself: str = "value"
) -> Expression:
    """The expression `predicate` says about one value — a value object's own — the parameter
    read as the field `itself`.

        value_expression_of(lambda v: 0 <= v <= 30)
            ->  All([value >= 0, value <= 30])
    """
    node, where = lambda_of(predicate)
    reading = _Reading(predicate, node, where, itself)
    return reading.read(node.body, frozenset({reading.root}))


def read_inside(function: Callable[..., Any], node: ast.expr) -> Expression:
    """The expression a part of `function`'s lambda says — the first argument of a `When(…)` a
    presentation writes — read with the lambda's own parameter as the record."""
    lambda_node, where = lambda_of(function)
    reading = _Reading(function, lambda_node, where)
    return reading.read(node, frozenset({reading.root}))
