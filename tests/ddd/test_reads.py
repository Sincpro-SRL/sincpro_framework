"""The reads of one aggregate, answered by one Feature from what the entity declared.

The project names its DTOs and its responses; the base of each DTO names the entity and the
response, and `EntityReads` answers all three from the `presentation`.
"""

from dataclasses import dataclass

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.ddd import (
    AggregateNotFound,
    Criteria,
    EntityReads,
    Get,
    GetMany,
    LiteralSearch,
    Match,
    MemoryRepository,
    Presentation,
    ResponsePaginatedQuery,
    ResponseRecord,
    ResponseRecords,
    Search,
    Specification,
)
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.reads import declared_by
from sincpro_framework.transport.failures import FailureKind, refined_failure_kind


@dataclass
class Account(Entity):
    code: str
    name: str = ""
    city: str = ""

    presentation = Presentation["Account"](
        search=lambda a: (Match.equal(a.code), Match.prefix(a.code), Match.contains(a.name)),
        order=lambda a: (a.code,),
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
