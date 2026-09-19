"""The repository with no database: what a Feature can be tested against without one.

The reason it is worth having and not faking: the filter is answered by `matches`, the same
evaluator the SQL translator is specified by, so a Feature that passes here and fails against
the engine has found a bug in the engine, not in the double.
"""

from dataclasses import dataclass, field
from datetime import datetime

import pytest

from sincpro_framework.ddd.criteria import (
    All,
    Condition,
    Criteria,
    Grouping,
    Level,
    Measure,
    Operator,
    Sort,
    parse_order,
)
from sincpro_framework.ddd.entity import ArchivableMixin, Entity
from sincpro_framework.ddd.entity_collection import EntityCollection
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    InvalidCriteria,
    StaleAggregate,
)
from sincpro_framework.ddd.memory_repository import MemoryRepository
from sincpro_framework.ddd.model_meta import FieldType
from sincpro_framework.ddd.pagination import Offset, Pagination


@dataclass
class Account(ArchivableMixin, Entity):
    code: str
    kind: str = "asset"
    balance: int = 0
    tags: list[str] = field(default_factory=list)


class Accounts(EntityCollection[Account]):
    pass


@pytest.fixture
def accounts() -> list[Account]:
    return [
        Account(
            code=f"{index:04d}", kind="asset" if index % 2 else "debt", balance=index * 10
        )
        for index in range(10)
    ]


@pytest.fixture
def ledger(accounts) -> MemoryRepository:
    return MemoryRepository(*accounts)


def test_it_answers_the_definition_off_the_annotations(ledger):
    meta = ledger.definition(Account)

    assert meta.identity == "id" and meta.aggregate == "Account"
    assert meta.fields["balance"].type is FieldType.INTEGER
    assert meta.fields["tags"].type is FieldType.TEXT_LIST


def test_a_filter_is_answered_by_the_same_evaluator_the_engine_is_checked_against(
    ledger, accounts
):
    asked = Criteria(
        where=All(
            all=[
                Condition(field="kind", value="asset"),
                Condition(field="balance", value=30, operator=Operator.GT),
            ]
        )
    )

    found = ledger.fetch_all(Accounts, asked)

    assert {one.code for one in found} == {
        one.code for one in accounts if one.kind == "asset" and one.balance > 30
    }


def test_a_page_walks_with_its_cursor_and_repeats_nothing(ledger, accounts):
    asked = Criteria(order=parse_order("code"), pagination=Pagination(limit=3))

    walked = [one for page in ledger.stream(Accounts, asked) for one in page]

    assert [one.code for one in walked] == sorted(one.code for one in accounts)
    assert len({one.id for one in walked}) == len(accounts)


def test_an_offset_skips_and_the_count_is_still_the_whole_set(ledger, accounts):
    page = ledger.search(
        Accounts,
        Criteria(
            order=parse_order("code"), pagination=Pagination(limit=3, strategy=Offset(rows=8))
        ),
    )

    assert len(page) == 2
    assert page.count is not None and page.count.value == len(accounts)


def test_what_the_aggregate_cannot_answer_is_dropped_and_said(ledger):
    page = ledger.search(Accounts, Criteria(where=Condition(field="legacy_flag", value=1)))

    assert [(one.field, one.reason) for one in page.dropped] == [
        ("legacy_flag", "unknown_field")
    ]


def test_the_short_readings_answer_the_same_as_the_adapter(ledger, accounts):
    assert ledger.exists(Accounts, Criteria(where=Condition(field="code", value="0003")))
    assert ledger.first(Accounts, Criteria(order=parse_order("code"))).code == "0000"
    assert (
        ledger.one(Accounts, Criteria(where=Condition(field="code", value="0003"))).balance
        == 30
    )
    assert ledger.get_by(Accounts, code="0004").balance == 40
    assert ledger.get_by(Accounts, code="nobody") is None
    assert ledger.pluck(
        Accounts, "balance", Criteria(where=Condition(field="kind", value="debt"))
    )
    assert ledger.distinct(Accounts, "kind") == ["asset", "debt"]
    with pytest.raises(ContractViolation):
        ledger.get_by(Accounts, kind="asset")
    with pytest.raises(ContractViolation):
        ledger.one(Accounts)


def test_folds_are_answered_over_the_whole_result_set(ledger, accounts):
    assert ledger.measures(
        Accounts, None, all=("sum", "balance"), top=("max", "balance")
    ) == {
        "all": sum(one.balance for one in accounts),
        "top": max(one.balance for one in accounts),
    }
    with pytest.raises(InvalidCriteria):
        ledger.measures(Accounts, None, evil="pg_sleep:balance")


def test_groups_count_fold_filter_and_order_like_the_adapter(ledger, accounts):
    asked = Criteria(
        grouping=Grouping(
            group_by=(Level(field="kind"),),
            measures={"weight": Measure(function="sum", field="balance")},
            order=(Sort(field="weight", descending=True),),
        )
    )

    buckets = ledger.group_by_levels(Accounts, asked)

    assert [one.value for one in buckets] == ["asset", "debt"]
    assert buckets[0].measures["weight"] > buckets[1].measures["weight"]
    assert sum(one.count for one in buckets) == len(accounts)
    opened = ledger.fetch_all(Accounts, buckets[0].criteria)
    assert len(opened) == buckets[0].count


def test_a_page_asked_gives_every_group_its_ids(ledger):
    asked = Criteria(
        order=parse_order("code"),
        pagination=Pagination(limit=2),
        grouping=Grouping(group_by=(Level(field="kind"),)),
    )

    buckets = ledger.group_by_levels(Accounts, asked)

    for bucket in buckets:
        assert len(bucket.ids) == 2 and bucket.cursor is not None
        assert ledger.browse(Accounts, bucket.ids).ids == bucket.ids


def test_a_date_grain_says_it_is_the_database_that_answers_it(ledger):
    with pytest.raises(ContractViolation, match="date grain"):
        ledger.group_by_levels(
            Accounts,
            Criteria(grouping=Grouping(group_by=(Level(field="created_at", grain="month"),))),
        )


def test_saving_raises_the_version_and_refuses_a_stale_write(ledger):
    fresh = Account(code="9000")

    ledger.save(fresh)
    assert fresh.version == 1

    mine = ledger.get(Accounts, fresh.id)
    theirs = Account(id=fresh.id, code="9000", version=fresh.version)
    assert mine is not None
    mine.balance = 5
    ledger.save(mine)
    assert mine.version == 2 and isinstance(mine.updated_at, datetime)

    theirs.balance = 7
    with pytest.raises(StaleAggregate):
        ledger.save(theirs)


def test_removing_archives_and_a_reading_leaves_the_archived_out(ledger, accounts):
    gone = accounts[0]

    ledger.remove(gone)

    assert gone.is_archived
    assert ledger.get(Accounts, gone.id) is None
    assert ledger.count(Accounts).value == len(accounts) - 1
    asked_for = ledger.search(
        Accounts,
        Criteria(
            where=Condition(field="archived_at", operator=Operator.IS_NULL, value=False)
        ),
    )
    assert [one.id for one in asked_for] == [gone.id]


def test_purge_takes_it_out_of_the_repository_altogether(ledger, accounts):
    ledger.purge(accounts[0])

    assert ledger.count(Accounts).value == len(accounts) - 1
    assert (
        ledger.search(
            Accounts,
            Criteria(
                where=Condition(field="archived_at", operator=Operator.IS_NULL, value=False)
            ),
        ).items
        == ()
    )


def test_it_stands_in_for_the_protocol_a_use_case_declares():
    from sincpro_framework.ddd.repository import Repository

    assert isinstance(MemoryRepository(), Repository)


def test_the_memory_repository_computes_the_measures_a_database_would():
    """**Including the two a plain function does not answer.** A Feature tested here and run
    against Postgres has to read the same numbers, so the percentile interpolates the way
    `percentile_cont` does rather than picking the nearest value.
    """
    store = MemoryRepository(
        Account(code="a", kind="asset", balance=10),
        Account(code="b", kind="asset", balance=20),
        Account(code="c", kind="asset", balance=30),
        Account(code="d", kind="asset", balance=40),
        Account(code="e", kind="liability", balance=40),
    )

    answered = store.measures(
        Accounts,
        None,
        total=("sum", "balance"),
        kinds=("count_distinct", "kind"),
        middle=Measure(function="percentile", field="balance", argument=0.5),
        p90=Measure(function="percentile", field="balance", argument=0.9),
    )

    assert answered["total"] == 140
    assert answered["kinds"] == 2
    assert answered["middle"] == 30, "the median of 10,20,30,40,40"
    assert answered["p90"] == 40


def test_a_percentile_interpolates_between_the_two_values_it_falls_between():
    store = MemoryRepository(
        Account(code="a", balance=1),
        Account(code="b", balance=2),
        Account(code="c", balance=3),
        Account(code="d", balance=4),
    )

    assert (
        store.measures(
            Accounts,
            None,
            middle=Measure(function="percentile", field="balance", argument=0.5),
        )["middle"]
        == 2.5
    )


def test_count_distinct_over_a_grouping_counts_within_each_bucket():
    store = MemoryRepository(
        Account(code="a", kind="asset", balance=10),
        Account(code="b", kind="asset", balance=10),
        Account(code="c", kind="asset", balance=20),
        Account(code="d", kind="liability", balance=30),
    )

    buckets = store.group_by_levels(
        Accounts,
        Criteria(
            grouping=Grouping(
                group_by=(Level(field="kind"),),
                measures={"balances": Measure(function="count_distinct", field="balance")},
            )
        ),
    )

    assert {one.value: one.measures["balances"] for one in buckets} == {
        "asset": 2,
        "liability": 1,
    }
