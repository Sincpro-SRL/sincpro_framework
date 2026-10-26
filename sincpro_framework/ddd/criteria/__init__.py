"""`sincpro_framework.ddd.criteria` — the filter language, and what is done with it.

    from sincpro_framework.ddd.criteria import Criteria, Condition, Operator, Today

Three layers, each importing only the one before it:

    domain/       the language: a criteria, a condition, a value that is read when it is used,
                  and what is intrinsic to them — `Node.combined`, `node.conditions()`,
                  `node.canonical()`, `Sort.parse`, `Period.range`, `Part.of`
    services/     the operations that need more than one piece of the language — evaluating a
                  filter in memory, reading its dynamic values, listing an axis
    entrypoint/   the facades over those operations: `Evaluation` (holds, matches), `Resolution`
                  (resolve, has_dynamic_values) and `Aggregation` (axis_values)

This module exposes the language a project writes and reads back, the three facades, and what
the rest of the framework reads of the language — `CursorKeys` (a cursor's keys, for a
repository's keyset page), `MULTI_VALUED` (the operators whose value is a list), `RESOLVERS` and
`ResolverMeta` (the dynamic values offered to a filter builder), and `Any_`, the class behind `Any`.

A filter written in code uses the names its JSON uses — `All`, `Any`, `Not` and `Condition`.
Context: the class behind `Any` is `Any_`, because the modules that define and read it also use
`typing.Any`, and the committed `criteria.schema.json` names it so.
"""

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
    Node,
    Not,
    Operator,
)
from sincpro_framework.ddd.criteria.domain.grouping import (
    Grouping,
    GroupingLevel,
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
from sincpro_framework.ddd.criteria.domain.sort import Sort
from sincpro_framework.ddd.criteria.domain.strict import TOLERANT
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
from sincpro_framework.ddd.criteria.entrypoint.aggregation import Aggregation
from sincpro_framework.ddd.criteria.entrypoint.evaluation import Evaluation
from sincpro_framework.ddd.criteria.entrypoint.resolution import Resolution

Any = Any_

__all__ = [
    "MULTI_VALUED",
    "RESOLVERS",
    "TEXT",
    "TOLERANT",
    "Aggregation",
    "All",
    "Any",
    "Any_",
    "Bucket",
    "Condition",
    "CountMode",
    "Criteria",
    "Cursor",
    "CursorKeys",
    "Evaluation",
    "Expression",
    "FromContext",
    "FromField",
    "Grain",
    "Grouping",
    "GroupingLevel",
    "Measure",
    "Node",
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
    "Resolution",
    "ResolutionContext",
    "ResolverMeta",
    "Sort",
    "Span",
    "Specification",
    "Today",
]
