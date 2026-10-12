"""`sincpro_framework.ddd.criteria` — the filter language, and what is done with it.

    from sincpro_framework.ddd.criteria import Criteria, Condition, Operator, Today

Three layers, each importing only the one before it:

    domain/       the language: a criteria, a condition, a value that is read when it is used,
                  and what is intrinsic to them (`combined`, `mapped`, `canonical`, `with_text`)
    services/     the pieces that need more than the language — calendar arithmetic, reading a
                  value, comparing — used by the entrypoints, never exposed
    entrypoint/   what other modules call: `resolve` a filter's dynamic values, `holds` and
                  `matches` to evaluate it over records in memory

This module exposes the domain and the entrypoints. Whoever needs something more reaches for it
by asking for it to be exposed here, not by importing a file.

A filter written in code uses the names its JSON uses — `All`, `Any`, `Not` and `Condition`.
Context: the class behind `Any` is `Any_`, because the modules that define and read it also use
`typing.Any`; `Any_` stays importable, as 3.10 published it.
"""

from sincpro_framework.ddd.criteria.domain.canonical import canonical
from sincpro_framework.ddd.criteria.domain.criteria import (
    Bucket,
    CountMode,
    Criteria,
    Specification,
)
from sincpro_framework.ddd.criteria.domain.expression import (
    MULTI_VALUED,
    All,
    Any_,
    Condition,
    Expression,
    Not,
    Operator,
    combined,
    conditions_of,
    expression_from,
    holds_text,
)
from sincpro_framework.ddd.criteria.domain.grouping import (
    Grouping,
    Level,
    Measure,
    Pivot,
    PivotCell,
)
from sincpro_framework.ddd.criteria.domain.pagination import (
    Cursor,
    CursorKeys,
    Offset,
    Pagination,
)
from sincpro_framework.ddd.criteria.domain.resolution import ResolutionContext
from sincpro_framework.ddd.criteria.domain.sort import Sort, parse_order
from sincpro_framework.ddd.criteria.domain.strict import TOLERANT
from sincpro_framework.ddd.criteria.domain.templates import with_text
from sincpro_framework.ddd.criteria.domain.values import (
    RESOLVERS,
    TEXT,
    FromContext,
    FromField,
    Grain,
    Now,
    Param,
    Part,
    Period,
    Periods,
    ResolverMeta,
    Span,
    Today,
)
from sincpro_framework.ddd.criteria.domain.walk import mapped
from sincpro_framework.ddd.criteria.entrypoint.axes import (
    axis_values,
    check_levels,
    check_measures,
    empty_measure,
    part_of,
)
from sincpro_framework.ddd.criteria.entrypoint.evaluation import holds, matches
from sincpro_framework.ddd.criteria.entrypoint.resolution import has_dynamic_values, resolve

Any = Any_

__all__ = [
    "MULTI_VALUED",
    "RESOLVERS",
    "TEXT",
    "TOLERANT",
    "All",
    "Any",
    "Any_",
    "Bucket",
    "Condition",
    "CountMode",
    "Criteria",
    "Cursor",
    "CursorKeys",
    "Expression",
    "FromContext",
    "FromField",
    "Grain",
    "Grouping",
    "Level",
    "Measure",
    "Not",
    "Now",
    "Offset",
    "Operator",
    "Pagination",
    "Param",
    "Part",
    "Period",
    "Periods",
    "Pivot",
    "PivotCell",
    "ResolutionContext",
    "ResolverMeta",
    "Sort",
    "Span",
    "Specification",
    "Today",
    "axis_values",
    "canonical",
    "check_levels",
    "check_measures",
    "combined",
    "conditions_of",
    "empty_measure",
    "expression_from",
    "has_dynamic_values",
    "holds",
    "holds_text",
    "mapped",
    "matches",
    "parse_order",
    "part_of",
    "resolve",
    "with_text",
]
