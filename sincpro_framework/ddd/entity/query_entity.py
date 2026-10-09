"""The criteria an entity's `presentation` turns into. They build a question; they never run it.

    accounts.search(matching(Account, "1.2.3"))          a short page: identity and display
    accounts.get(account_id)                             the stored record: every scalar
    accounts.get(account_id, detail=detail_of(Account))  that record, with what the detail names

A select must not load the detail, and a get is one record, never a page.
"""

from sincpro_framework.ddd.criteria import (
    Any_,
    Criteria,
    Expression,
    Pagination,
    Specification,
)
from sincpro_framework.ddd.entity.model_meta import presentation_of

NAME_SEARCH_LIMIT = 8
"""How many a select lists: what fits under a field without scrolling."""


def matching(model: type, text: str) -> Criteria:
    """The select for a literal: any declared match holds, a short page of references.

        in      Account, "1.2.3"    →  out  code = 1.2.3 OR code starts with 1.2.3
                                            OR name contains 1.2.3, first 8, {id, display}
        in      Account, "   "      →  out  no filter, first 8 in the list order
        in      Line, "x"           →  out  an empty page: Line has nothing to search

    The page is in the entity's list order, which the store applies when the criteria names
    none. Each record comes back as a reference: its identity and its display field.
    """
    literal = text.strip()
    reference = Specification({})
    if not literal:
        return Criteria(
            pagination=Pagination(limit=NAME_SEARCH_LIMIT), specification=reference
        )
    conditions: list[Expression] = [
        match.condition(literal) for match in presentation_of(model).search
    ]
    if not conditions:
        return Criteria(pagination=Pagination(limit=0), specification=reference)
    return Criteria(
        where=conditions[0] if len(conditions) == 1 else Any_(any=conditions),
        pagination=Pagination(limit=NAME_SEARCH_LIMIT),
        specification=reference,
    )


def detail_of(model: type) -> Criteria | None:
    """What the entity's detail brings, for `get(identity, detail=...)`. `None` when it
    declares none: the stored record alone.

        in      Move (detail=(a.number, Expand(a.lines, 300)))
        out     Criteria(specification={"number": {}, "lines": {limit 300}})
    """
    return presentation_of(model).detail
