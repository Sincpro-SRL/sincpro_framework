"""`sincpro_framework.ddd.criteria` — a package now, same public names as when it was one
module: the filter language (`criteria.py`), how it is answered in memory (`evaluate.py`), and
how a page is asked for (`pagination.py`).

    from sincpro_framework.ddd.criteria import Criteria, Condition, Operator

Still the whole story in one import; the three files are how it is organized underneath.
"""

from sincpro_framework.ddd.criteria.criteria import (
    MULTI_VALUED,
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
    parse_order,
)
from sincpro_framework.ddd.criteria.evaluate import matches
from sincpro_framework.ddd.criteria.pagination import Cursor, CursorKeys, Offset, Pagination

__all__ = [
    "MULTI_VALUED",
    "All",
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
    "matches",
    "parse_order",
]
