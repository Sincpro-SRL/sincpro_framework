"""The reads of one aggregate, answered by one Feature from what the entity answers.

The project names its DTOs and its responses; the base of each DTO names the entity and the
response, and `EntityReads` answers them from the entity's `DEFAULT_*` class methods. What a
caller's criteria names wins over each of them.
"""

from dataclasses import dataclass

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.ddd import (
    TEXT,
    AggregateNotFound,
    All,
    Any,
    Condition,
    Criteria,
    EntityReads,
    Get,
    GetMany,
    LiteralSearch,
    MemoryRepository,
    Operator,
    ResponsePaginatedQuery,
    ResponseRecord,
    ResponseRecords,
    Search,
    Sort,
    Specification,
    presentation_of,
)
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.model_meta import describe_class
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.reads import declared_by
from sincpro_framework.transport.failures import FailureKind, refined_failure_kind


@dataclass
class Account(Entity):
    code: str
    name: str = ""
    city: str = ""

    @classmethod
    def DEFAULT_ORDER(cls) -> tuple[Sort, ...]:
        return (Sort(field="code"),)

    @classmethod
    def DEFAULT_LITERAL_SEARCH(cls) -> Criteria:
        return Criteria(
            where=Any(
                any=[
                    Condition(field="code", value=TEXT),
                    Condition(field="code", operator=Operator.STARTS_WITH, value=TEXT),
                    Condition(field="name", operator=Operator.LIKE, value=TEXT),
                ]
            )
        )


class ResponseAccount(ResponseRecord):
    account: Account


class ResponseListAccounts(ResponsePaginatedQuery):
    accounts: list[Account]


class ResponseAccounts(ResponseRecords):
    accounts: list[Account]


class QueryGetManyAccounts(GetMany[Account, ResponseAccounts]):
    pass


class QueryGetAccount(Get[Account, ResponseAccount]):
    pass


class QueryFindAccounts(LiteralSearch[Account, ResponseListAccounts]):
    pass


class QueryListAccounts(Search[Account, ResponseListAccounts]):
    pass


def bus_with(*accounts: Account) -> UseFramework:
    bus = UseFramework("reads", log_after_execution=False)
    bus.add_dependency("repository", MemoryRepository().add(*accounts))

    @bus.feature(
        [QueryGetAccount, QueryGetManyAccounts, QueryFindAccounts, QueryListAccounts]
    )
    class AccountReads(EntityReads[Account]):
        pass

    return bus


CAJA = Account(code="1.2.3", name="Caja", city="La Paz")
BANCO = Account(code="1.2.30", name="Banco", city="Santa Cruz")
CHICA = Account(code="1.2.1.2.3", name="Caja chica", city="La Paz")


def test_the_dto_names_its_entity_and_its_response():
    assert declared_by(QueryGetAccount) == (Account, ResponseAccount)
    assert declared_by(QueryFindAccounts) == (Account, ResponseListAccounts)


def test_get_answers_the_one_record():
    bus = bus_with(CAJA, BANCO)

    answer = bus(QueryGetAccount(id=CAJA.id), ResponseAccount)

    assert answer.account.code == "1.2.3"
    assert answer.model_meta_data is not None
    assert answer.model_dump(mode="json")["account"]["city"] == "La Paz"


def test_get_narrowed_by_the_caller_keeps_the_identity_and_what_it_named():
    bus = bus_with(CAJA)
    asked = Criteria(specification=Specification({"code": Criteria()}))

    written = bus(QueryGetAccount(id=CAJA.id, criteria=asked), ResponseAccount).model_dump(
        mode="json"
    )

    assert written["account"] == {"id": CAJA.id, "code": "1.2.3"}


def test_a_missing_record_is_not_found_on_every_wire():
    bus = bus_with(CAJA)

    with pytest.raises(AggregateNotFound) as raised:
        bus(QueryGetAccount(id="gone"), ResponseAccount)

    assert refined_failure_kind(raised.value) is FailureKind.NOT_FOUND


def test_a_literal_search_is_the_short_page_of_identity_and_display():
    bus = bus_with(CAJA, BANCO, CHICA)

    answer = bus(QueryFindAccounts(text="1.2.3"), ResponseListAccounts)

    assert [one.code for one in answer.accounts] == ["1.2.3", "1.2.30"]
    assert answer.model_dump(mode="json")["accounts"][0] == {"id": CAJA.id, "name": "Caja"}


def test_a_blank_literal_is_the_first_page_in_the_declared_order():
    bus = bus_with(BANCO, CAJA, CHICA)

    answer = bus(QueryFindAccounts(text="  "), ResponseListAccounts)

    assert [one.code for one in answer.accounts] == ["1.2.1.2.3", "1.2.3", "1.2.30"]


def test_a_search_answers_the_criteria_in_the_declared_order():
    bus = bus_with(BANCO, CHICA, CAJA)

    answer = bus(QueryListAccounts(), ResponseListAccounts)

    assert [one.code for one in answer.accounts] == ["1.2.1.2.3", "1.2.3", "1.2.30"]


def test_a_read_is_extended_by_overriding_it_and_calling_super():
    bus = UseFramework("reads-extended", log_after_execution=False)
    bus.add_dependency("repository", MemoryRepository().add(CAJA))
    seen: list[str] = []

    @bus.feature(QueryGetAccount)
    class AccountReads(EntityReads[Account]):
        def get(self, dto: QueryGetAccount) -> ResponseAccount:
            seen.append(f"before {dto.id}")
            answer = super().get(dto)
            seen.append(f"after {answer.account.code}")
            return answer

    bus(QueryGetAccount(id=CAJA.id), ResponseAccount)

    assert seen == [f"before {CAJA.id}", "after 1.2.3"]


def test_a_repository_named_otherwise_is_handed_in_by_overriding_reads_from():
    bus = UseFramework("reads-named", log_after_execution=False)
    bus.add_dependency("ledger", MemoryRepository().add(CAJA))

    @bus.feature(QueryGetAccount)
    class AccountReads(EntityReads[Account]):
        ledger: MemoryRepository

        def reads_from(self) -> MemoryRepository:
            return self.ledger

    assert bus(QueryGetAccount(id=CAJA.id), ResponseAccount).account.code == "1.2.3"


def test_without_a_repository_the_feature_says_which_dependency_it_needs():
    bus = UseFramework("reads-without", log_after_execution=False)

    @bus.feature(QueryGetAccount)
    class AccountReads(EntityReads[Account]):
        pass

    with pytest.raises(ContractViolation, match="'repository'"):
        bus(QueryGetAccount(id="x"), ResponseAccount)


def test_a_dto_that_does_not_name_its_entity_is_refused():
    class QueryBare(Get):  # type: ignore[type-arg]
        pass

    with pytest.raises(ContractViolation, match="Get\\[Account, ResponseAccount\\]"):
        declared_by(QueryBare)


def test_get_many_answers_the_records_in_the_order_asked_and_names_the_missing():
    bus = bus_with(CAJA, BANCO, CHICA)

    answer = bus(
        QueryGetManyAccounts(ids=[BANCO.id, "gone", CAJA.id, BANCO.id]), ResponseAccounts
    )

    assert [one.code for one in answer.accounts] == ["1.2.30", "1.2.3"]
    assert answer.missing == ["gone"]
    written = answer.model_dump(mode="json")
    assert set(written) == {"accounts", "missing", "model_meta_data", "dropped"}
    assert written["accounts"][0]["city"] == "Santa Cruz"


def test_get_many_narrowed_by_the_caller_brings_identity_and_what_it_named():
    bus = bus_with(CAJA, BANCO)
    asked = Criteria(specification=Specification({}))

    written = bus(
        QueryGetManyAccounts(ids=[CAJA.id, BANCO.id], criteria=asked), ResponseAccounts
    ).model_dump(mode="json")

    assert written["accounts"] == [
        {"id": CAJA.id, "name": "Caja"},
        {"id": BANCO.id, "name": "Banco"},
    ]


def test_get_many_of_nothing_is_an_empty_list():
    bus = bus_with(CAJA)

    answer = bus(QueryGetManyAccounts(ids=[]), ResponseAccounts)

    assert answer.accounts == [] and answer.missing == []


# --- What the entity answers is the default; what the caller names wins ---------------------


@dataclass
class Ledger(Entity):
    code: str
    name: str = ""
    city: str = ""
    active: bool = True

    @classmethod
    def DEFAULT_GET_ID(cls) -> str:
        return "code"

    @classmethod
    def DEFAULT_READING(cls) -> Specification:
        return Specification.model_validate({"code": {}, "name": {}})

    @classmethod
    def DEFAULT_ORDER(cls) -> tuple[Sort, ...]:
        return (Sort(field="code", descending=True),)

    @classmethod
    def DEFAULT_LITERAL_SEARCH(cls) -> Criteria:
        return Criteria(
            where=All(
                all=[
                    Condition(field="name", operator=Operator.LIKE, value=TEXT),
                    Condition(field="active", value=True),
                ]
            )
        )


class ResponseLedger(ResponseRecord):
    ledger: Ledger


class ResponseLedgers(ResponseRecords):
    ledgers: list[Ledger]


class ResponseListLedgers(ResponsePaginatedQuery):
    ledgers: list[Ledger]


class QueryGetLedger(Get[Ledger, ResponseLedger]):
    pass


class QueryGetManyLedgers(GetMany[Ledger, ResponseLedgers]):
    pass


class QueryFindLedgers(LiteralSearch[Ledger, ResponseListLedgers]):
    pass


class QueryListLedgers(Search[Ledger, ResponseListLedgers]):
    pass


CASH = Ledger(code="1.1", name="Caja", city="La Paz")
BANK = Ledger(code="1.2", name="Banco", city="Santa Cruz")
CLOSED = Ledger(code="1.3", name="Caja vieja", city="Sucre", active=False)


def ledgers(*records: Ledger) -> UseFramework:
    bus = UseFramework("ledgers", log_after_execution=False)
    bus.add_dependency("repository", MemoryRepository().add(*records))

    @bus.feature([QueryGetLedger, QueryGetManyLedgers, QueryFindLedgers, QueryListLedgers])
    class LedgerReads(EntityReads[Ledger]):
        pass

    return bus


def test_get_finds_a_record_by_the_key_the_entity_names():
    bus = ledgers(CASH, BANK)

    answer = bus(QueryGetLedger(id="1.2"), ResponseLedger)

    assert answer.ledger.id == BANK.id
    with pytest.raises(AggregateNotFound):
        bus(QueryGetLedger(id=BANK.id), ResponseLedger)


def test_get_many_finds_by_that_key_and_names_the_missing_by_it():
    bus = ledgers(CASH, BANK)

    answer = bus(QueryGetManyLedgers(ids=["1.2", "9.9", "1.1"]), ResponseLedgers)

    assert [one.code for one in answer.ledgers] == ["1.2", "1.1"]
    assert answer.missing == ["9.9"]


def test_get_reads_what_the_entity_answers_when_the_caller_names_nothing():
    bus = ledgers(CASH)

    for asked in (QueryGetLedger(id="1.1"), QueryGetLedger(id="1.1", criteria=Criteria())):
        written = bus(asked, ResponseLedger).model_dump(mode="json")
        assert written["ledger"] == {"id": CASH.id, "code": "1.1", "name": "Caja"}


def test_a_caller_specification_replaces_the_reading_even_to_ask_for_more():
    bus = ledgers(CASH)
    asked = Criteria(specification=Specification({"city": Criteria()}))

    written = bus(QueryGetLedger(id="1.1", criteria=asked), ResponseLedger).model_dump(
        mode="json"
    )

    assert written["ledger"] == {"id": CASH.id, "city": "La Paz"}


def test_a_caller_filter_adds_to_the_key_instead_of_replacing_it():
    bus = ledgers(CASH, CLOSED)
    only_active = Criteria(where=Condition(field="active", value=True))

    with pytest.raises(AggregateNotFound):
        bus(QueryGetLedger(id="1.3", criteria=only_active), ResponseLedger)
    assert (
        bus(QueryGetLedger(id="1.1", criteria=only_active), ResponseLedger).ledger.code
        == "1.1"
    )


def test_a_search_takes_the_entity_order_and_reading_unless_the_caller_names_them():
    bus = ledgers(CASH, BANK, CLOSED)

    by_default = bus(QueryListLedgers(), ResponseListLedgers)
    by_caller = bus(
        QueryListLedgers(criteria=Criteria(order=(Sort(field="name"),))), ResponseListLedgers
    )

    assert [one.code for one in by_default.ledgers] == ["1.3", "1.2", "1.1"]
    assert set(by_default.model_dump(mode="json")["ledgers"][0]) == {"id", "code", "name"}
    assert [one.name for one in by_caller.ledgers] == ["Banco", "Caja", "Caja vieja"]


def test_a_scope_merged_into_the_criteria_does_not_hide_the_entity_defaults():
    bus = ledgers(CASH, BANK)
    scoped = Criteria(where=Condition(field="city", value="La Paz")).merged_with(Criteria())

    answer = bus(QueryListLedgers(criteria=scoped), ResponseListLedgers)

    assert answer.model_dump(mode="json")["ledgers"] == [
        {"id": CASH.id, "code": "1.1", "name": "Caja"}
    ]


def test_a_literal_fills_the_template_and_keeps_what_it_asks_besides_the_text():
    bus = ledgers(CASH, BANK, CLOSED)

    found = bus(QueryFindLedgers(text="caja"), ResponseListLedgers)
    blank = bus(QueryFindLedgers(text=" "), ResponseListLedgers)

    assert [one.code for one in found.ledgers] == ["1.1"]
    assert [one.code for one in blank.ledgers] == ["1.2", "1.1"]


def test_meta_publishes_what_the_entity_answers():
    meta = describe_class(Ledger, "id")

    assert (meta.get_id, meta.display, meta.default_order) == ("code", "name", "-code")
    assert meta.detail == Criteria(specification=Ledger.DEFAULT_READING())
    assert meta.search == Ledger.DEFAULT_LITERAL_SEARCH()


# --- What the entity answers is checked when the class is described --------------------------


def _described(entity: type) -> None:
    presentation_of.cache_clear()
    presentation_of(entity)


def test_a_key_that_is_not_a_scalar_field_of_its_own_is_refused():
    @dataclass
    class Unknown(Entity):
        @classmethod
        def DEFAULT_GET_ID(cls) -> str:
            return "code"

    @dataclass
    class Flag(Entity):
        on: bool = False

        @classmethod
        def DEFAULT_GET_ID(cls) -> str:
            return "on"

    with pytest.raises(ContractViolation, match="not a field of Unknown"):
        _described(Unknown)
    with pytest.raises(ContractViolation, match="a boolean field"):
        _described(Flag)


def test_a_reading_must_be_a_specification_of_its_fields():
    @dataclass
    class Filtered(Entity):
        @classmethod
        def DEFAULT_READING(cls) -> Specification:
            return Criteria(where=Condition(field="id", value="x"))  # type: ignore[return-value]

    @dataclass
    class Misnamed(Entity):
        @classmethod
        def DEFAULT_READING(cls) -> Specification:
            return Specification({"title": Criteria()})

    with pytest.raises(ContractViolation, match="never which records"):
        _described(Filtered)
    with pytest.raises(ContractViolation, match="title"):
        _described(Misnamed)


def test_a_literal_search_is_a_where_holding_text_on_fields_that_answer_it():
    @dataclass
    class NoText(Entity):
        name: str = ""

        @classmethod
        def DEFAULT_LITERAL_SEARCH(cls) -> Criteria:
            return Criteria(where=Condition(field="name", value="x"))

    @dataclass
    class Paged(Entity):
        name: str = ""

        @classmethod
        def DEFAULT_LITERAL_SEARCH(cls) -> Criteria:
            return Criteria(
                where=Condition(field="name", operator=Operator.LIKE, value=TEXT),
                order=(Sort(field="name"),),
            )

    @dataclass
    class PrefixOnNumber(Entity):
        size: int = 0

        @classmethod
        def DEFAULT_LITERAL_SEARCH(cls) -> Criteria:
            return Criteria(
                where=Condition(field="size", operator=Operator.STARTS_WITH, value=TEXT)
            )

    with pytest.raises(ContractViolation, match="never uses TEXT"):
        _described(NoText)
    with pytest.raises(ContractViolation, match="sets order"):
        _described(Paged)
    with pytest.raises(ContractViolation, match="cannot answer"):
        _described(PrefixOnNumber)


def test_a_default_declared_as_anything_but_a_class_method_is_refused():
    with pytest.raises(
        ContractViolation, match="Declare it as `@classmethod def DEFAULT_GET_ID"
    ):

        @dataclass
        class Constant(Entity):
            code: str = ""
            DEFAULT_GET_ID = "code"  # type: ignore[assignment]

    with pytest.raises(ContractViolation, match="DEFAULT_ORDER is staticmethod"):

        @dataclass
        class Static(Entity):
            @staticmethod
            def DEFAULT_ORDER() -> tuple[Sort, ...]:  # type: ignore[override]
                return ()

    with pytest.raises(ContractViolation, match="DEFAULT_DISPLAY is a field"):

        @dataclass
        class Annotated(Entity):
            DEFAULT_DISPLAY: str  # type: ignore[assignment]


def test_the_entity_defaults_need_nothing_declared():
    @dataclass
    class Note(Entity):
        name: str = ""
        size: int = 0

    read = presentation_of(Note)

    assert (read.get_id, read.display, read.order, read.detail) == (
        "id",
        "name",
        (Sort(field="id", descending=True),),
        None,
    )
    assert read.search == Criteria(
        where=Condition(field="name", operator=Operator.LIKE, value=TEXT)
    )


def test_a_literal_fills_text_inside_a_list_too():
    @dataclass
    class Coded(Entity):
        code: str = ""

        @classmethod
        def DEFAULT_LITERAL_SEARCH(cls) -> Criteria:
            return Criteria(
                where=Condition(field="code", operator=Operator.IN, value=[TEXT, "0"])
            )

    bus = UseFramework("coded", log_after_execution=False)
    bus.add_dependency(
        "repository",
        MemoryRepository().add(Coded(code="7"), Coded(code="0"), Coded(code="9")),
    )

    class ResponseCoded(ResponsePaginatedQuery):
        coded: list[Coded]

    class QueryFindCoded(LiteralSearch[Coded, ResponseCoded]):
        pass

    @bus.feature(QueryFindCoded)
    class CodedReads(EntityReads[Coded]):
        pass

    found = bus(QueryFindCoded(text="7"), ResponseCoded)

    assert sorted(one.code for one in found.coded) == ["0", "7"]
