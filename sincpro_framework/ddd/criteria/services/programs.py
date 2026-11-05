"""A filter compiled once into the function that answers it, and kept.

    program = program_of(All([state = "posted", total > 100]))
    [row for row in rows if program(partial(getattr, row))]

Walking the tree for every record asks the same questions of the same nodes again and again: which
kind of node, which field, which operator. A program asks them once, when it is built, and answers
each record with closures that only read and compare. Measured over 200,000 records: 1.45 µs a
record walking the tree, 0.54 µs compiled (docs/spec/model.md §8).

Context: a program answers exactly as `holds` does — the same `compare`, the same `FromField`, the
same crossing of a relation — so the parity suite holds both. It never reads a dynamic value: a
filter that holds one is resolved first, then compiled.
"""

import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria.domain.expression import (
    All,
    Any_,
    Condition,
    Expression,
    Not,
    Through,
)
from sincpro_framework.ddd.criteria.domain.values import FromField
from sincpro_framework.ddd.criteria.services.comparison import comparator, compare

type Program = Callable[[Callable[[str], Any]], bool]
"""A compiled filter: given how a record's fields are read, whether the record answers it."""

KEPT = 1024
"""How many programs are kept, the least recently used let go first."""


def held(value: Any) -> list[Any]:
    """The records a relation holds, as a list: several, one, or none.

    held([line_a, line_b])  ->  [line_a, line_b]
    held(customer)          ->  [customer]
    held(page)              ->  page.items           a page of records
    held(None)              ->  []
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    page = getattr(value, "items", None)
    if isinstance(page, tuple):
        return list(page)  # a page of records (`EntityCollection`)
    return [value]


def reader(record: Any) -> Callable[[str], Any]:
    """How a record's fields are read: by key for a mapping, by attribute otherwise."""
    if isinstance(record, Mapping):
        return record.get
    return partial(getattr, record)


def _condition(condition: Condition) -> Program:
    """One comparison, with its field, operator and value fixed — decided once (`comparator`): a
    value that names another field (`FromField`) reads it, and a `None` there never holds."""
    field, operator, value = condition.field, condition.operator, condition.value
    if isinstance(value, FromField):
        other = value.name

        def against_a_field(read: Callable[[str], Any]) -> bool:
            compared = read(other)
            return compared is not None and compare(read(field), operator, compared)

        return against_a_field

    answered = comparator(operator, value)
    return lambda read: answered(read(field))


def compiled(expression: Expression | None) -> Program:
    """The program of `expression`, built now.

        compiled(None)                          ->  every record answers
        compiled(size > 1)                      ->  compare(read("size"), GT, 1)
        compiled(All([a, b]))                   ->  a(read) and b(read), stopping at the first no
        compiled(Through("lines", qty > 10))    ->  some line answers qty > 10

    1. No filter: every record answers.
    2. A condition: its comparison (`_condition`).
    3. `All` / `Any_`: their parts compiled, stopping at the first that decides.
    4. `Not`: the part does not hold.
    5. `Through`: some record the relation holds answers the selection.
    Final: anything else is not a filter — `InvalidCriteria`.
    """
    match expression:
        case None:
            return lambda read: True
        case Condition():
            return _condition(expression)
        case All():
            every = tuple(compiled(part) for part in expression.all)
            return lambda read: all(part(read) for part in every)
        case Any_():
            some = tuple(compiled(part) for part in expression.any)
            return lambda read: any(part(read) for part in some)
        case Not():
            negated = compiled(expression.negate)
            return lambda read: not negated(read)
        case Through():
            relation, inner = expression.through, compiled(expression.selection)
            return lambda read: any(inner(reader(one)) for one in held(read(relation)))
    raise exceptions.InvalidCriteria(f"not a filter expression: {expression!r}")


_programs: "OrderedDict[str, Program]" = OrderedDict()
_lock = threading.Lock()


def program_of(expression: Expression | None) -> Program:
    """The program of `expression`, compiled the first time and kept — by the expression's text,
    which tells `Decimal('1')` from `'1'` — the least recently used let go past `KEPT`."""
    key = repr(expression)
    with _lock:
        found = _programs.get(key)
        if found is not None:
            _programs.move_to_end(key)
            return found
    built = compiled(expression)
    with _lock:
        _programs[key] = built
        _programs.move_to_end(key)
        while len(_programs) > KEPT:
            _programs.popitem(last=False)
    return built
