"""`sincpro_framework.ddd.criteria` — a package now, same public names as when it was one
module: the filter language (`criteria.py`), how it is answered in memory (`evaluate.py`), and
how a page is asked for (`pagination.py`).

    from sincpro_framework.ddd.criteria import Criteria, Condition, Operator

Still the whole story in one import; the three files are how it is organized underneath.

A filter written in code uses the names its JSON uses — `All`, `Any`, `Not` and `Condition`.
Context: the class behind `Any` is `Any_`, because the modules that define and read it also use
`typing.Any`; `Any_` stays importable, as 3.10 published it.
"""

from sincpro_framework.ddd.criteria.criteria import (
    MULTI_VALUED,
    TEXT,
    All,
    Any_,
    Bucket,
    Condition,
    CountMode,
    Criteria,
    Expression,
    Grouping,
    Level,
    Measure,
    Not,
    Operator,
    Pivot,
    PivotCell,
    Sort,
    Specification,
    combined,
    conditions_of,
    expression_from,
    holds_text,
    parse_order,
    with_text,
)
from sincpro_framework.ddd.criteria.evaluate import holds, matches
from sincpro_framework.ddd.criteria.pagination import Cursor, CursorKeys, Offset, Pagination
from sincpro_framework.ddd.criteria.strict import TOLERANT

Any = Any_

__all__ = [
    "TOLERANT",
    "MULTI_VALUED",
    "TEXT",
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
    "Grouping",
    "Level",
    "Measure",
    "Not",
    "Offset",
    "Operator",
    "Pagination",
    "Pivot",
    "PivotCell",
    "Sort",
    "Specification",
    "combined",
    "conditions_of",
    "expression_from",
    "holds_text",
    "holds",
    "matches",
    "parse_order",
    "with_text",
]
