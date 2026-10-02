"""Two flavours of one repository: the generic one answers everything, and a view of one
aggregate names the domain's questions — through the same store, in the same transaction, and
never past its hooks, scope or version check."""

import inspect

import pytest
from sqlalchemy import func

from sincpro_framework.ddd import (
    AggregateRepository,
    Analyzes,
    Condition,
    Criteria,
    EntityCollection,
    IRepository,
    MemoryRepository,
    ReadsAggregates,
    StoreCapabilities,
    Transacts,
    WritesAggregates,
    WritesInBulk,
)
from sincpro_framework.ddd.exceptions import ContractViolation, StaleAggregate
from sincpro_framework.orm import DatabaseAggregateRepository, Repository

from .engines import fresh
from .ledger_models import Account, Owner, account_table, bank, declare


class Accounts(DatabaseAggregateRepository[Account]):
    def of(self, owner: Owner) -> EntityCollection[Account]:
        return self.search(Criteria(where=Condition(field="owner_id", value=owner.id)))

    def overdrawn_risk(self, below: int) -> EntityCollection[Account]:
        asked = Criteria()
        return self.run(self.statement(asked).where(account_table.c.balance < below), asked)


@pytest.fixture
def ledger(engine_url: str) -> Repository:
    declare()
    return Repository(fresh(engine_url, bank.metadata, enforce_foreign_keys=True))


@pytest.fixture
def ana(ledger: Repository) -> Owner:
    owner = Owner(name="ana")
    ledger.save(owner)
    return owner


# ── the capabilities a store declares ────────────────────────────────────────────────────


def test_the_database_repository_claims_every_capability():
    assert issubclass(Repository, ReadsAggregates)
    assert issubclass(Repository, WritesAggregates)
    assert issubclass(Repository, Analyzes)
    assert issubclass(Repository, WritesInBulk)
    assert issubclass(Repository, Transacts)


def test_the_memory_repository_does_not_claim_a_transaction():
    assert issubclass(MemoryRepository, Analyzes)
    assert issubclass(MemoryRepository, WritesInBulk)
    assert not issubclass(MemoryRepository, Transacts)
    assert MemoryRepository().capabilities == StoreCapabilities(percentiles=True)


def test_what_the_engine_honours_is_read_off_the_engine(ledger):
    capabilities = ledger.capabilities
    on_postgres = ledger.database.engine.dialect.name == "postgresql"
    assert capabilities.row_locks is on_postgres
    assert capabilities.skip_locked is on_postgres
    assert capabilities.percentiles is on_postgres
    assert capabilities.savepoints


def test_a_store_that_declares_nothing_promises_nothing_beyond_the_baseline():
    assert StoreCapabilities() == StoreCapabilities(
        row_locks=False, skip_locked=False, nowait=False, savepoints=False, percentiles=False
    )


# ── the aggregate is the class's ─────────────────────────────────────────────────────────


def test_the_aggregate_is_read_off_the_class(ledger, ana):
    accounts = Accounts(ledger)
    account = Account(owner_id=ana.id, balance=10)
    accounts.save(account)

    assert accounts.aggregate is Account
    found = accounts.get(account.id)
    assert found is not None and found.id == account.id
    assert accounts.of(ana).ids == [account.id]


def test_a_view_nobody_names_is_handed_its_aggregate(ledger, ana):
    owners = DatabaseAggregateRepository(ledger, Owner)
    found = owners.get_by(name="ana")
    assert found is not None and found.id == ana.id


def test_a_view_without_an_aggregate_is_refused(ledger):
    with pytest.raises(ContractViolation, match="which aggregate"):
        DatabaseAggregateRepository(ledger)


def test_a_named_view_cannot_be_pointed_at_another_aggregate(ledger):
    with pytest.raises(ContractViolation, match="holds Account"):
        Accounts(ledger, Owner)  # pyright: ignore[reportArgumentType]


def test_the_baseline_view_runs_on_any_store(ana):
    memory = MemoryRepository(ana)
    owners = AggregateRepository(memory, Owner)
    assert owners.count().value == 1
    assert owners.exists()


# ── through the store, never around it ───────────────────────────────────────────────────


def test_a_stale_save_through_the_view_is_refused_like_any_other(ledger, ana):
    accounts = Accounts(ledger)
    account = Account(owner_id=ana.id, balance=10)
    accounts.save(account)
    first, second = accounts.get(account.id), accounts.get(account.id)
    assert first is not None and second is not None
    first.withdraw(1)
    accounts.save(first)
    second.withdraw(2)
    with pytest.raises(StaleAggregate):
        accounts.save(second)


def test_inside_a_unit_of_work_the_view_is_the_same_class_on_the_same_transaction(
    ledger, ana
):
    accounts = Accounts(ledger)
    with pytest.raises(RuntimeError):
        with accounts.context() as unit:
            assert isinstance(unit, Accounts)
            unit.save(Account(owner_id=ana.id, balance=10))
            assert len(unit.of(ana).ids) == 1
            raise RuntimeError("undo")
    assert accounts.of(ana).ids == []


def test_a_narrowed_view_keeps_its_named_questions_behind_the_scope(ledger, ana):
    other = Owner(name="bea")
    ledger.save(other)
    accounts = Accounts(ledger)
    accounts.save(
        [Account(owner_id=ana.id, balance=1), Account(owner_id=other.id, balance=2)]
    )

    anas = accounts.narrowed(Criteria(where=Condition(field="owner_id", value=ana.id)))
    assert isinstance(anas, Accounts)
    assert anas.of(other).ids == []
    assert anas.count().value == 1


# ── what the database repository adds, with the aggregate given ─────────────────────────


def test_analysis_and_bulk_writes_take_the_aggregate_from_the_view(ledger, ana):
    accounts = Accounts(ledger)
    accounts.save(
        [Account(owner_id=ana.id, balance=10), Account(owner_id=ana.id, balance=30)]
    )

    assert accounts.measures(total=("sum", "balance")) == {"total": 40}
    assert accounts.group_by(["owner_id"])[0]["count"] == 2
    assert (
        accounts.update_all(
            Criteria(where=Condition(field="owner_id", value=ana.id)), {"balance": 5}
        )
        == 2
    )
    assert accounts.distinct("balance") == [5]


def test_the_door_underneath_answers_the_usual_envelope(ledger, ana):
    accounts = Accounts(ledger)
    accounts.save(
        [Account(owner_id=ana.id, balance=10), Account(owner_id=ana.id, balance=300)]
    )

    risky = accounts.overdrawn_risk(below=100)
    assert [one.balance for one in risky] == [10]
    assert "account" in accounts.explain().sql


def test_the_session_is_sqlalchemy_whole_inside_the_unit_of_work(ledger, ana):
    accounts = Accounts(ledger)
    accounts.save(Account(owner_id=ana.id, balance=7))
    with accounts.context() as unit:
        total = unit.session.scalar(func.sum(account_table.c.balance).select())
    assert total == 7


# ── the two flavours never drift apart ──────────────────────────────────────────────────

HOOKS = {
    f"{when}_{moment}"
    for when in ("before", "after")
    for moment in ("save", "create", "update", "remove", "archive")
} | {"after_read", "after_search"}

LEFT_TO_THE_STORE = {
    **{
        hook: "a hook of the store, overridden on the store, fired for every view"
        for hook in HOOKS
    },
    "capabilities": "the engine's, read through view.repository",
    "fingerprint": "a cache key of the store, taken with the aggregate by QueryCache",
    "prepare": "the translator's preparation, not a question a Feature asks",
}


def public(cls: type) -> set[str]:
    return {name for name in dir(cls) if not name.startswith("_")}


def parameters(function: object) -> list[str]:
    asked = inspect.signature(function).parameters  # type: ignore[arg-type]
    return [name for name in asked if name not in ("self", "target")]


@pytest.mark.parametrize(
    ("store", "view"),
    [(Repository, DatabaseAggregateRepository), (IRepository, AggregateRepository)],
    ids=["orm", "ddd"],
)
def test_every_method_of_the_store_is_on_its_view_or_left_to_the_store_by_name(store, view):
    """A method added to the repository fails here until the view repeats it or this test
    says why it stays on the store."""
    assert public(store) - public(view) == set(LEFT_TO_THE_STORE) & public(store)


@pytest.mark.parametrize(
    ("store", "view"),
    [(Repository, DatabaseAggregateRepository), (IRepository, AggregateRepository)],
    ids=["orm", "ddd"],
)
def test_the_view_asks_what_the_store_asks_less_the_aggregate(store, view):
    for name in sorted(public(store) & public(view)):
        on_store, on_view = getattr(store, name), getattr(view, name)
        if not callable(on_store) or isinstance(
            inspect.getattr_static(store, name), property
        ):
            continue
        assert parameters(on_view) == parameters(on_store), name
