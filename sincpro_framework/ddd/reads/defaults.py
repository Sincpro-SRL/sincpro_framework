"""The criteria an entity's `DEFAULT_*` class methods turn into. They build a question; they
never run it.

    accounts.search(matching(Account, "1.2.3"))          a short page: identity and display
    accounts.get(account_id)                             the stored record: every scalar
    accounts.get(account_id, detail=detail_of(Account))  that record, with its DEFAULT_READING

A select must not load the detail, and a get is one record, never a page.
"""

from sincpro_framework.ddd.criteria import Criteria, Pagination, Specification, with_text
from sincpro_framework.ddd.entity.entity_meta import presentation_of

NAME_SEARCH_LIMIT = 8
"""How many a select lists: what fits under a field without scrolling."""


def matching(model: type, text: str) -> Criteria:
    """The select for a literal: the entity's `DEFAULT_LITERAL_SEARCH` filled with it, a short
    page of references.

        in      Account (code starts with TEXT or name like TEXT), "1.2.3"
        out     code starts with 1.2.3 OR name contains 1.2.3, first 8, {id, display}
        in      Account, "   "      →  out  no filter on the text, first 8 in the list order
        in      Line, "x"           →  out  an empty page: Line has nothing to search

    1. A blank literal drops every condition holding `TEXT`; what the template asks besides the
       text (an `active = true`) still applies.
    2. With no template, a literal finds nothing: an empty page, never every record.
    Final: the page is in the entity's list order, which the store applies when the criteria
    names none. Each record comes back as a reference: its identity and its display field.
    """
    literal = text.strip()
    reference = Specification({})
    template = presentation_of(model).search
    if template is None:
        limit = NAME_SEARCH_LIMIT if not literal else 0
        return Criteria(pagination=Pagination(limit=limit), specification=reference)
    return Criteria(
        where=with_text(template.where, literal),
        pagination=Pagination(limit=NAME_SEARCH_LIMIT),
        specification=reference,
    )


def detail_of(model: type) -> Criteria | None:
    """What one record brings: the entity's `DEFAULT_READING` as the criteria a read starts
    from, for `get(identity, detail=...)`. `None` when it is every scalar and no relation.

        in      Move (DEFAULT_READING = {"number": {}, "lines": {}})
        out     Criteria(specification={"number": {}, "lines": {}})
    """
    return presentation_of(model).detail
