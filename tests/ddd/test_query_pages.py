"""A reading paged by one name on the way in and on the way out: an answer's `cursor`, sent back as it
came, goes on from where the page ended — the criteria never rebuilt — and `pages` walks them all.
"""

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd import Criteria, Query, ResponsePaginatedQuery

INVOICES = [f"F-{n}" for n in range(1, 8)]


class QueryInvoices(Query):
    customer: str


class ResponseInvoices(ResponsePaginatedQuery):
    invoices: list[str]


class Asked(DataTransferObject):
    """What each call was asked: the customer, the page size, where it went on from."""

    calls: list[tuple[str, int, str | None]] = []


def _billing(asked: Asked) -> UseFramework:
    billing = UseFramework("paging-billing", log_after_execution=False)

    @billing.feature(QueryInvoices)
    class ListInvoices(Feature):
        """A page of the customer's invoices, its cursor the position after it."""

        def execute(self, dto: QueryInvoices) -> ResponseInvoices:
            asked.calls.append((dto.customer, dto.criteria.limit, dto.criteria.cursor))
            start = int(dto.criteria.cursor or 0)
            end = start + dto.criteria.limit
            return ResponseInvoices(
                invoices=INVOICES[start:end],
                cursor=str(end) if end < len(INVOICES) else None,
            )

    return billing


def test_the_cursor_sent_back_goes_on_in_the_criteria_the_use_case_reads():
    query = QueryInvoices.model_validate(
        {"customer": "c-1", "criteria": {"pagination": {"limit": 3}}, "cursor": "3"}
    )

    assert (query.criteria.cursor, query.criteria.limit) == ("3", 3)


def test_the_cursor_wins_over_a_token_the_criteria_carries():
    criteria = Criteria.model_validate({"pagination": {"limit": 3}}).resuming_from("old")

    assert QueryInvoices(customer="c-1", criteria=criteria, cursor="6").criteria.cursor == "6"
    assert QueryInvoices(customer="c-1", criteria=criteria).criteria.cursor == "old"


def test_every_wire_says_what_the_cursor_is():
    described = QueryInvoices.model_json_schema()["properties"]["cursor"]

    assert "sent back as it is" in described["description"]


def test_every_page_is_read_once_on_the_same_criteria_until_the_last():
    asked = Asked()
    billing = _billing(asked)
    criteria = Criteria.model_validate({"pagination": {"limit": 3}})

    pages = list(
        QueryInvoices(customer="c-1", criteria=criteria).pages(billing, ResponseInvoices)
    )

    assert [page.invoices for page in pages] == [INVOICES[0:3], INVOICES[3:6], INVOICES[6:7]]
    assert asked.calls == [("c-1", 3, None), ("c-1", 3, "3"), ("c-1", 3, "6")]


def test_pages_stop_at_the_cap_the_caller_gives():
    asked = Asked()
    billing = _billing(asked)
    criteria = Criteria.model_validate({"pagination": {"limit": 2}})

    pages = list(
        QueryInvoices(customer="c-1", criteria=criteria).pages(
            billing, ResponseInvoices, max_pages=2
        )
    )

    assert [page.invoices for page in pages] == [INVOICES[0:2], INVOICES[2:4]]
    assert len(asked.calls) == 2


def test_pages_are_read_as_they_are_iterated_never_ahead():
    asked = Asked()
    billing = _billing(asked)

    walking = QueryInvoices(customer="c-1").pages(billing, ResponseInvoices)
    assert asked.calls == []
    next(walking)

    assert len(asked.calls) == 1


def test_any_caller_of_the_bus_s_shape_pages_the_same():
    answered: list[str | None] = []

    def client(
        query: QueryInvoices, response_type: type[ResponseInvoices]
    ) -> ResponseInvoices:
        """A client of another service — the same call the bus is."""
        answered.append(query.cursor)
        return response_type(invoices=[], cursor=None if query.cursor else "next")

    assert len(list(QueryInvoices(customer="c-1").pages(client, ResponseInvoices))) == 2
    assert answered == [None, "next"]
