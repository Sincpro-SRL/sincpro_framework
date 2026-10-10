"""What the entity answers about how it is read, against a mapped class: SQLAlchemy replaces
the class attributes of the fields with its own, and none of that reaches the entity's
`DEFAULT_*` class methods, which name fields as text and are never a field.
"""

from dataclasses import dataclass, field

import pytest
from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.orm import map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.ddd import (
    TEXT,
    AggregateNotFound,
    Any,
    Condition,
    Criteria,
    EntityReads,
    Get,
    GetMany,
    Operator,
    ResponseRecord,
    ResponseRecords,
    Sort,
    Specification,
    detail_of,
    matching,
)
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.exceptions import ContractViolation


@dataclass
class Ledger(Entity):
    code: str
    name: str = ""
    entries: list["Entry"] = field(default_factory=list)

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

    @classmethod
    def DEFAULT_ORDER(cls) -> tuple[Sort, ...]:
        return (Sort(field="code"),)

    @classmethod
    def DEFAULT_READING(cls) -> Specification:
        return Specification.model_validate(
            {"code": {}, "name": {}, "entries": {"pagination": {"limit": 300}}}
        )


@dataclass
class Entry(Entity):
    ledger_id: str
    amount: int = 0

    @classmethod
    def DEFAULT_ORDER(cls) -> tuple[Sort, ...]:
        return (Sort(field="amount", descending=True),)


books = registry()
ledger_table = entity_table(
    "presented_ledger",
    books.metadata,
    Column("code", Text, nullable=False),
    Column("name", Text, nullable=False),
)
entry_table = entity_table(
    "presented_entry",
    books.metadata,
    Column("ledger_id", Text, ForeignKey("presented_ledger.id"), nullable=False),
    Column("amount", Integer, nullable=False),
)
map_aggregates(books, {Ledger: ledger_table, Entry: entry_table})


@pytest.fixture
def ledgers() -> Repository:
    database = Database("sqlite://")
    books.metadata.create_all(database.engine)
    store = Repository(database)
    with store.context() as repository:
        for code, name in [
            ("1.2.3", "Caja"),
            ("1.2.30", "Banco"),
            ("1.2.1.2.3", "Caja chica"),
            ("4", "Descuento 50% off"),
            ("5", "a_b"),
            ("6", "axb"),
        ]:
            ledger = Ledger(code=code, name=name)
            repository.save(ledger)
            repository.save(Entry(ledger_id=ledger.id, amount=len(name)))
    return store


def test_the_mapped_class_publishes_what_the_entity_declared():
    meta = describe(Ledger)

    assert (meta.get_id, meta.display) == ("id", "name")
    assert meta.search == Ledger.DEFAULT_LITERAL_SEARCH()
    assert meta.default_order == "code"
    assert "presentation" not in meta.fields


def test_a_prefix_finds_the_children_and_not_a_code_that_only_contains_it(ledgers):
    found = ledgers.search(Ledger, matching(Ledger, "1.2.3"))

    assert [one.code for one in found] == ["1.2.3", "1.2.30"]


def test_a_blank_literal_lists_the_first_ones_in_the_declared_order(ledgers):
    found = ledgers.search(Ledger, matching(Ledger, ""))

    assert [one.code for one in found] == ["1.2.1.2.3", "1.2.3", "1.2.30", "4", "5", "6"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("50%", ["Descuento 50% off"]), ("a_b", ["a_b"]), ("CAJA", ["Caja", "Caja chica"])],
)
def test_a_literal_is_read_as_itself_as_in_memory(ledgers, text, expected):
    found = ledgers.search(Ledger, matching(Ledger, text))

    assert sorted(one.name for one in found) == expected


def test_get_without_a_detail_resolves_no_relation(ledgers):
    one = ledgers.search(Ledger, matching(Ledger, "1.2.30")).items[0]

    found = ledgers.get(Ledger, one.id)

    assert found is not None and found.code == "1.2.30"
    assert "entries" not in found.__dict__.get("_sincpro_resolved", {})


def test_get_with_the_detail_resolves_what_it_names_onto_the_one_record(ledgers):
    one = ledgers.search(Ledger, matching(Ledger, "1.2.30")).items[0]

    found = ledgers.get(Ledger, one.id, detail=detail_of(Ledger))

    assert found is not None and found.code == "1.2.30"
    entries = found.__dict__["_sincpro_resolved"]["entries"]
    assert [entry.amount for entry in entries] == [len("Banco")]


class ResponseLedger(ResponseRecord):
    ledger: Ledger


class QueryGetLedger(Get[Ledger, ResponseLedger]):
    pass


def test_get_through_the_bus_brings_the_declared_detail_with_its_relation(ledgers):
    bus = UseFramework("presented-reads", log_after_execution=False)
    bus.add_dependency("repository", ledgers)

    @bus.feature(QueryGetLedger)
    class LedgerReads(EntityReads[Ledger]):
        pass

    caja = ledgers.search(Ledger, matching(Ledger, "1.2.3")).items[0]
    written = bus(QueryGetLedger(id=caja.id), ResponseLedger).model_dump(mode="json")

    assert written["ledger"]["code"] == "1.2.3"
    assert [one["amount"] for one in written["ledger"]["entries"]["items"]] == [4]
    with pytest.raises(AggregateNotFound):
        bus(QueryGetLedger(id="gone"), ResponseLedger)


class ResponseLedgers(ResponseRecords):
    ledgers: list[Ledger]


class QueryGetManyLedgers(GetMany[Ledger, ResponseLedgers]):
    pass


def test_get_many_through_the_bus_brings_each_record_with_its_relation(ledgers):
    bus = UseFramework("presented-get-many", log_after_execution=False)
    bus.add_dependency("repository", ledgers)

    @bus.feature(QueryGetManyLedgers)
    class LedgerReads(EntityReads[Ledger]):
        pass

    by_code = {one.code: one.id for one in ledgers.search(Ledger, matching(Ledger, ""))}
    asked = [by_code["4"], "gone", by_code["1.2.3"]]
    written = bus(QueryGetManyLedgers(ids=asked), ResponseLedgers).model_dump(mode="json")

    assert [one["code"] for one in written["ledgers"]] == ["4", "1.2.3"]
    assert [one["entries"]["items"][0]["amount"] for one in written["ledgers"]] == [17, 4]
    assert written["missing"] == ["gone"]


def test_a_relation_comes_in_its_own_entity_order_when_the_reading_names_none():
    database = Database("sqlite://")
    books.metadata.create_all(database.engine)
    store = Repository(database)
    ledger = Ledger(code="9", name="Ordenado")
    with store.context() as repository:
        repository.save(ledger)
        for amount in (2, 7, 4):
            repository.save(Entry(ledger_id=ledger.id, amount=amount))

    read = store.get(Ledger, ledger.id, detail=detail_of(Ledger))

    assert read is not None
    assert [one.amount for one in read.entries] == [7, 4, 2]


def test_a_key_other_than_the_identity_must_be_unique_on_disk():
    @dataclass
    class Coded(Entity):
        code: str = ""

        @classmethod
        def DEFAULT_GET_ID(cls) -> str:
            return "code"

    @dataclass
    class Unique(Entity):
        code: str = ""

        @classmethod
        def DEFAULT_GET_ID(cls) -> str:
            return "code"

    keyed = registry()
    map_aggregates(
        keyed,
        {
            Coded: entity_table("loose_code", keyed.metadata, Column("code", Text)),
            Unique: entity_table(
                "unique_code", keyed.metadata, Column("code", Text, unique=True)
            ),
        },
    )

    with pytest.raises(ContractViolation, match="loose_code.code is not unique"):
        describe(Coded)
    assert describe(Unique).get_id == "code"
