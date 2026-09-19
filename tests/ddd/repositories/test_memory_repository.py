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
from sincpro_framework.ddd.criteria.pagination import Offset, Pagination
from sincpro_framework.ddd.entity import ArchivableMixin, AuditedMixin, Entity
from sincpro_framework.ddd.entity.entity_collection import EntityCollection
from sincpro_framework.ddd.entity.model_meta import FieldType
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    InvalidCriteria,
    StaleAggregate,
)
from sincpro_framework.ddd.repositories.memory_repository import MemoryRepository


@dataclass
class Account(ArchivableMixin, Entity):
    code: str
    kind: str = "asset"
    balance: int = 0
    tags: list[str] = field(default_factory=list)


@dataclass
class AuditedAccount(AuditedMixin, Entity):
    """The other convention, for the stamping the engine also does."""

    code: str = ""
    balance: int = 0


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


def test_archiving_puts_it_away_and_a_reading_leaves_it_out(ledger, accounts):
    gone = accounts[0]

    ledger.archive(gone)

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


def test_remove_takes_it_out_of_the_repository_altogether(ledger, accounts):
    ledger.remove(accounts[0])

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
    from sincpro_framework.ddd.repositories.repository import Repository

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


def test_the_implementations_declare_the_protocol_they_answer():
    """Both repositories inherit `Repository` rather than only matching its shape — that is
    what makes the type checker compare signatures. This test pins the declaration; the
    signature check itself is pyright's, in `make lint`."""
    from sincpro_framework.ddd.repositories.repository import Repository
    from sincpro_framework.orm.sqlalchemy.repository import Repository as SqlRepository

    assert Repository in MemoryRepository.__mro__
    assert Repository in SqlRepository.__mro__


def test_a_class_that_only_has_the_names_is_no_longer_mistaken_for_a_repository():
    """What the `Protocol` could not refuse. As a protocol this passed: `@runtime_checkable`
    compares names and nothing else, so five methods with absurd signatures satisfied it. As an
    abstract class it does not, and the type checker compares the signatures besides."""
    from sincpro_framework.ddd.repositories.repository import Repository

    class EveryNameWrongSignature:
        def get(self, number: int) -> None: ...
        def search(self) -> None: ...
        def count(self, a, b, c, d) -> None: ...
        def save(self) -> None: ...
        def remove(self, x, y, z) -> None: ...

    assert not isinstance(EveryNameWrongSignature(), Repository)


def test_a_store_that_forgot_a_method_cannot_be_built_at_all():
    """The other half of what the protocol never gave: nothing could stop a store missing
    `remove` from being instantiated and failing at the first call.

    The class is built dynamically because it is deliberately wrong — the point is what
    happens at runtime, and writing it out would only ask the type checker to accept broken
    code in our own tree (it refuses it, which is the same guarantee from the other side).
    """
    import pytest

    from sincpro_framework.ddd.repositories.repository import Repository

    halfway = type(
        "Halfway",
        (Repository,),
        {
            "get": lambda self, target, identity: None,
            "search": lambda self, target, criteria=None: None,
            "count": lambda self, target, criteria=None: None,
            "save": lambda self, record: None,
            # remove is missing
        },
    )

    with pytest.raises(TypeError, match="abstract"):
        halfway()


def test_one_save_takes_one_aggregate_or_several(ledger, accounts):
    """One door, whatever the caller happens to hold: a record, a list, or a page."""
    repository = MemoryRepository()
    one, two, three = accounts[0], accounts[1], accounts[2]

    repository.save(one)
    repository.save([two, three])

    assert {found.code for found in repository.fetch_all(Account)} == {
        one.code,
        two.code,
        three.code,
    }
    assert all(found.version == 1 for found in repository.fetch_all(Account))


def test_one_save_takes_a_page_straight_back(ledger):
    page = ledger.search(Accounts)
    for found in page:
        found.balance = 99

    ledger.save(page)  # what a reading answered, handed straight to a write

    assert all(found.balance == 99 for found in ledger.fetch_all(Accounts))


def test_one_archive_takes_one_or_several(ledger, accounts):
    ledger.archive(accounts[0])
    ledger.archive([accounts[1], accounts[2]])

    assert all(one.is_archived for one in accounts[:3])
    living = {one.code for one in ledger.fetch_all(Accounts)}
    assert not living & {one.code for one in accounts[:3]}


def test_saving_nothing_is_nothing(ledger):
    before = ledger.count(Account).value

    ledger.save([])
    ledger.remove([])

    assert ledger.count(Account).value == before


def test_archive_refuses_an_aggregate_that_cannot_say_it_was_archived(ledger):
    """`Thing` has no `archived_at` to stamp. Refused by name rather than silently doing
    nothing, and refused before touching any of them so a mixed batch is all or none."""
    from sincpro_framework.ddd.exceptions import ContractViolation

    @dataclass
    class Plain(Entity):
        label: str = ""

    with pytest.raises(ContractViolation, match="cannot be archived"):
        ledger.archive(Plain())


def test_a_mixed_batch_is_refused_whole_rather_than_half_archived(ledger, accounts):
    from sincpro_framework.ddd.exceptions import ContractViolation

    @dataclass
    class Plain(Entity):
        label: str = ""

    with pytest.raises(ContractViolation, match="cannot be archived"):
        ledger.archive([accounts[0], Plain()])

    assert not accounts[0].is_archived  # the archivable one was not touched either


def test_a_row_lock_is_refused_rather_than_quietly_ignored():
    """The double swallowed `for_update` with `**_`, so a Feature that claims a row under a
    lock passed every test here and then raced in production, where the engine refuses the
    same call outside `context()`. A double that disagrees with the engine is worse than no
    double."""
    from sincpro_framework.ddd.exceptions import ContractViolation

    repository = MemoryRepository()
    account = Account(code="1010")
    repository.save(account)

    with pytest.raises(ContractViolation, match="unit of work"):
        repository.get(Account, account.id, for_update=True)
    with pytest.raises(ContractViolation, match="unit of work"):
        repository.search(Accounts, for_update=True)

    assert repository.get(Account, account.id, for_update=False) is not None


def test_an_argument_the_store_does_not_know_is_refused():
    """`**_` turned every typo into a no-op."""
    repository = MemoryRepository()
    with pytest.raises(TypeError):
        repository.get(Account, "whatever", for_updatee=True)  # type: ignore[call-arg]


def test_who_wrote_it_is_stamped_the_way_the_engine_stamps_it():
    """The engine takes an `actor` and stamps `created_by`/`updated_by` on the flush. The double
    took none, so an aggregate came back with `None` in a test and with a name in production —
    the divergence a double exists to prevent."""
    acting: dict[str, str | None] = {"user": "ana"}
    repository = MemoryRepository(actor=lambda: acting["user"])

    client = AuditedAccount(code="1010")
    repository.save(client)
    stored = repository.get(AuditedAccount, client.id)
    assert stored is not None
    assert stored.created_by == "ana" and stored.updated_by is None

    acting["user"] = "bruno"
    stored.balance = 10
    repository.save(stored)
    assert stored.created_by == "ana" and stored.updated_by == "bruno"


def test_without_an_actor_those_fields_stay_none_as_they_do_on_the_engine():
    repository = MemoryRepository()
    client = AuditedAccount(code="2010")
    repository.save(client)

    stored = repository.get(AuditedAccount, client.id)
    assert stored is not None
    assert stored.created_by is None and stored.updated_by is None


def test_an_actor_that_raises_writes_nothing_rather_than_failing_the_save():
    """The same bargain the engine makes: a write with nobody behind it is `None`, not a
    failure."""

    def broken() -> str:
        raise RuntimeError("no request here")

    repository = MemoryRepository(actor=broken)
    client = AuditedAccount(code="3010")
    repository.save(client)

    stored = repository.get(AuditedAccount, client.id)
    assert stored is not None and stored.created_by is None
