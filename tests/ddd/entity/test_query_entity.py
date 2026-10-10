"""The entity says once how it is read, and the select, the list and the detail follow from it.

Its `DEFAULT_*` class methods answer the key, what a record brings, the order, the display and
how a text finds it; `Entity` answers each when a class says nothing. Upper case, so a field
called `order` collides with nothing, and checked when the class is described.
"""

from dataclasses import dataclass, field, fields

import pytest

from sincpro_framework import ProgrammingError
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    NAME_SEARCH_LIMIT,
    TEXT,
    Entity,
    Presentation,
    detail_of,
    matching,
)
from sincpro_framework.ddd.criteria import (
    Any_,
    Condition,
    Criteria,
    Operator,
    Sort,
    Specification,
)
from sincpro_framework.ddd.entity.entity_meta import describe_class, presentation_of
from sincpro_framework.ddd.query import ResponsePaginatedQuery
from sincpro_framework.ddd.repositories import AggregateRepository


@dataclass
class Partner(Entity):
    code: str
    name: str = ""
    city: str = ""


@dataclass
class Account(Entity):
    code: str
    name: str = ""
    order: int = 0
    lines: list[str] = field(default_factory=list)

    @classmethod
    def DEFAULT_LITERAL_SEARCH(cls) -> Criteria:
        return Criteria(
            where=Any_(
                any=[
                    Condition(field="code", value=TEXT),
                    Condition(field="code", operator=Operator.STARTS_WITH, value=TEXT),
                    Condition(field="name", operator=Operator.LIKE, value=TEXT),
                ]
            )
        )

    @classmethod
    def DEFAULT_ORDER(cls) -> tuple[Sort, ...]:
        return (Sort(field="code"),)

    @classmethod
    def DEFAULT_READING(cls) -> Specification:
        return Specification.model_validate(
            {"code": {}, "name": {}, "lines": {"pagination": {"limit": 300}}}
        )


@dataclass
class Journal(Account):
    posted: str = ""

    @classmethod
    def DEFAULT_DISPLAY(cls) -> str:
        return "code"

    @classmethod
    def DEFAULT_ORDER(cls) -> tuple[Sort, ...]:
        return (Sort(field="posted", descending=True), Sort(field="code"))

    @classmethod
    def DEFAULT_READING(cls) -> Specification:
        return Specification.model_validate({"code": {}, "lines": {"specification": {}}})


@dataclass
class Line(Entity):
    amount: int = 0


class ResponseListAccounts(ResponsePaginatedQuery):
    accounts: list[Account]


@pytest.fixture
def accounts() -> AggregateRepository[Account]:
    repository = MemoryRepository()
    for code, name in [
        ("1.2.3", "Caja"),
        ("1.2.30", "Banco"),
        ("1.2.1.2.3", "Caja chica"),
        ("4", "Descuento 50% off"),
        ("5", "a_b"),
        ("6", "axb"),
    ]:
        repository.save(Account(code=code, name=name))
    return AggregateRepository(repository, Account)


def test_presentation_and_the_defaults_are_never_fields():
    names = [one.name for one in fields(Account)]
    assert "presentation" not in names and "DEFAULT_ORDER" not in names
    assert "presentation" not in describe_class(Account, "id").fields


def test_nothing_declared_makes_the_field_called_name_the_display_searched_by_containment():
    meta = describe_class(Partner, "id")

    assert meta.display == "name"
    assert meta.search == Criteria(
        where=Condition(field="name", operator=Operator.LIKE, value=TEXT)
    )
    assert meta.default_order == "-id"
    assert (meta.get_id, meta.detail) == ("id", None)


def test_a_class_with_no_name_field_has_no_display_and_nothing_to_search():
    meta = describe_class(Line, "id")

    assert (meta.display, meta.search) == ("", None)


def test_the_definition_publishes_what_the_entity_declared():
    meta = describe_class(Account, "id")

    assert meta.display == "name"
    assert meta.search == Account.DEFAULT_LITERAL_SEARCH()
    assert meta.default_order == "code"
    assert meta.detail is not None and meta.detail.specification is not None
    assert meta.detail.specification.named == ["code", "name", "lines"]
    assert meta.detail.specification.root["lines"].pagination.limit == 300
    assert meta.detail.specification.root["lines"].specification is None


def test_a_subclass_overrides_some_defaults_and_inherits_the_others():
    meta = describe_class(Journal, "id")

    assert meta.search == Account.DEFAULT_LITERAL_SEARCH()
    assert meta.display == "code"
    assert meta.default_order == "-posted,code"
    assert presentation_of(Journal).order == (
        Sort(field="posted", descending=True),
        Sort(field="code"),
    )
    lines = detail_of(Journal)
    assert lines is not None and lines.specification is not None
    assert lines.specification.root["lines"].specification == Specification({})


def test_an_unknown_field_is_refused_when_the_class_is_described():
    @dataclass
    class Typo(Entity):
        code: str

        @classmethod
        def DEFAULT_DISPLAY(cls) -> str:
            return "cde"

    @dataclass
    class HintTypo(Entity):
        code: str

        presentation = Presentation["HintTypo"](
            readonly=lambda a: a.cde  # type: ignore[attr-defined]
        )

    with pytest.raises(ProgrammingError, match="DEFAULT_DISPLAY answers 'cde'"):
        describe_class(Typo, "id")
    with pytest.raises(ProgrammingError, match="HintTypo.presentation names cde"):
        describe_class(HintTypo, "id")


def test_a_prefix_on_a_number_is_refused_instead_of_widening_the_search():
    @dataclass
    class Numbered(Entity):
        number: int = 0

        @classmethod
        def DEFAULT_LITERAL_SEARCH(cls) -> Criteria:
            return Criteria(
                where=Condition(field="number", operator=Operator.STARTS_WITH, value=TEXT)
            )

    with pytest.raises(ProgrammingError, match="searches number by starts with"):
        describe_class(Numbered, "id")


def test_matching_fills_the_template_for_a_short_page_of_references():
    criteria = matching(Account, " 1.2.3 ")

    assert criteria.where == Any_(
        any=[
            Condition(field="code", value="1.2.3", operator=Operator.EQ),
            Condition(field="code", value="1.2.3", operator=Operator.STARTS_WITH),
            Condition(field="name", value="1.2.3", operator=Operator.LIKE),
        ]
    )
    assert criteria.pagination.limit == NAME_SEARCH_LIMIT
    assert criteria.specification == Specification({})


def test_a_prefix_finds_the_children_and_not_a_code_that_only_contains_it(accounts):
    found = accounts.search(matching(Account, "1.2.3"))

    assert [one.code for one in found] == ["1.2.3", "1.2.30"]


def test_a_blank_literal_lists_the_first_ones_in_the_declared_order(accounts):
    found = accounts.search(matching(Account, "   "))

    assert [one.code for one in found] == ["1.2.1.2.3", "1.2.3", "1.2.30", "4", "5", "6"]


def test_an_entity_with_nothing_to_search_answers_an_empty_page():
    repository = MemoryRepository()
    repository.save(Line(amount=1))

    assert list(repository.search(Line, matching(Line, "1"))) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [("50%", ["Descuento 50% off"]), ("a_b", ["a_b"]), ("CAJA", ["Caja", "Caja chica"])],
)
def test_a_literal_is_read_as_itself(accounts, text, expected):
    found = accounts.search(matching(Account, text))

    assert sorted(one.name for one in found) == expected


def test_a_select_carries_the_identity_and_the_display_on_the_wire(accounts):
    asked = matching(Account, "caja")
    answer = ResponseListAccounts.of(accounts.search(asked), asked).model_dump()

    assert all(set(record) == {"id", "name"} for record in answer["accounts"])
    assert list(answer["entity_meta_data"]["fields"]) == ["id", "name"]


def test_get_without_a_detail_is_the_stored_record(accounts):
    one = accounts.first(Criteria(where=Condition(field="code", value="1.2.3")))
    assert one is not None

    assert accounts.get(one.id) == one


def test_get_with_the_detail_is_still_the_one_record(accounts):
    one = accounts.first(Criteria(where=Condition(field="code", value="1.2.3")))
    assert one is not None

    assert accounts.get(one.id, detail=detail_of(Account)) == one


def test_get_checks_the_detail_like_search_checks_a_specification(accounts):
    """An unknown name is dropped and the record still comes back, as a search would."""
    one = accounts.first()
    assert one is not None

    asked = Criteria(specification=Specification({"gone": Criteria()}))
    assert accounts.get(one.id, detail=asked) == one
