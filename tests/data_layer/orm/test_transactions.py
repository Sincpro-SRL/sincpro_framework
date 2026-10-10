"""A unit of work is configured where it begins, takes the locks it asks for, and names what the
engine refused — so the races it loses are the ones a fresh read can win."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete, insert, text

from sincpro_framework.data_layer.orm.sqlalchemy.domain.transaction import Isolation, Writes
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure import transaction_opening
from sincpro_framework.data_layer.orm.sqlalchemy.services.workflows import (
    unit_of_work as unit_of_work_module,
)
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd.exceptions import (
    ConstraintViolation,
    ContractViolation,
    DuplicateAggregate,
    TimedOut,
)

from .engines import fresh
from .ledger_models import Account, Owner, account_table, bank, declare


@pytest.fixture
def ledger(engine_url: str) -> Repository:
    declare()
    return Repository(fresh(engine_url, bank.metadata, enforce_foreign_keys=True))


@pytest.fixture
def opened(ledger: Repository) -> Account:
    owner = Owner(name="ana")
    ledger.save(owner)
    account = Account(owner_id=owner.id, balance=100)
    ledger.save(account)
    return account


def on_postgres(repository: Repository) -> bool:
    return repository.database.engine.dialect.name == "postgresql"


def setting(unit: Repository, name: str) -> str:
    return unit.session.execute(text(f"SHOW {name}")).scalar_one()


# ── what the engine refused, named ───────────────────────────────────────────────────────


def test_a_second_owner_with_the_same_name_is_a_duplicate(ledger):
    ledger.save(Owner(name="ana"))
    with pytest.raises(DuplicateAggregate):
        ledger.save(Owner(name="ana"))


def test_an_account_of_an_owner_that_does_not_exist_is_a_constraint_violation(ledger):
    with pytest.raises(ConstraintViolation, match="a foreign key"):
        ledger.save(Account(owner_id="nobody", balance=1))


def test_an_overdraft_the_table_forbids_is_a_constraint_violation(ledger, opened):
    opened.withdraw(500)
    with pytest.raises(ConstraintViolation, match="a rule the table holds"):
        ledger.save(opened)


def test_a_constraint_violation_is_not_retried(ledger, opened):
    attempts = []

    def overdraw() -> None:
        attempts.append(1)
        with ledger.context() as unit:
            account = unit.get(Account, opened.id)
            account.withdraw(500)
            unit.save(account)

    with pytest.raises(ConstraintViolation):
        ledger.retrying(overdraw)
    assert len(attempts) == 1


# ── the transaction is configured where it begins ────────────────────────────────────────


def test_serializable_is_what_the_transaction_runs_under(ledger, opened):
    with ledger.context(isolation=Isolation.SERIALIZABLE) as unit:
        unit.get(Account, opened.id)
        if on_postgres(ledger):
            assert setting(unit, "transaction_isolation") == "serializable"


def test_a_weaker_level_runs_under_a_stronger_one_where_that_is_all_there_is(ledger, opened):
    with ledger.context(isolation=Isolation.REPEATABLE_READ) as unit:
        assert unit.get(Account, opened.id) is not None
        if on_postgres(ledger):
            assert setting(unit, "transaction_isolation") == "repeatable read"


def test_a_read_only_unit_of_work_refuses_every_write(ledger, opened):
    with ledger.context(read_only=True) as unit:
        account = unit.get(Account, opened.id)
        account.withdraw(10)
        with pytest.raises(ContractViolation, match="read_only"):
            unit.save(account)
        with pytest.raises(ContractViolation, match="read_only"):
            unit.remove(account)
    assert ledger.get(Account, opened.id).balance == 100


def test_what_a_read_only_unit_of_work_read_is_usable_after_it_and_after_commit_runs(
    ledger, opened
):
    heard: list[str] = []
    with ledger.context(read_only=True) as unit:
        report = unit.get(Account, opened.id)
        unit.after_commit(lambda: heard.append("reported"))
    assert report is not None and report.balance == 100
    assert heard == ["reported"]


def test_retrying_inside_a_unit_of_work_is_refused(ledger):
    with ledger.context() as unit:
        with pytest.raises(ContractViolation, match="around the context"):
            unit.retrying(lambda: None)


def test_a_read_only_unit_of_work_is_read_only_in_postgres_too(ledger, opened):
    if not on_postgres(ledger):
        pytest.skip("only Postgres has read-only transactions")
    with pytest.raises(Exception, match="read-only transaction"):
        with ledger.context(read_only=True) as unit:
            unit.session.execute(
                insert(account_table).values(id="x", owner_id="y", balance=1)
            )


def test_a_timeout_bounds_every_statement(ledger, monkeypatch):
    warned: list[str] = []

    class Heard:
        def warning(self, message: str) -> None:
            warned.append(message)

    monkeypatch.setattr(transaction_opening, "logger", Heard())
    with ledger.context(timeout=2.5) as unit:
        if on_postgres(ledger):
            assert setting(unit, "statement_timeout") == "2500ms"
    assert bool(warned) is not on_postgres(ledger)


def test_the_engine_options_are_passed_as_they_come(ledger, opened):
    if not on_postgres(ledger):
        pytest.skip("the option is Postgres'")
    with ledger.context(engine={"postgresql_readonly": True}) as unit:
        assert setting(unit, "transaction_read_only") == "on"


def test_a_nested_unit_of_work_joins_and_cannot_reconfigure(ledger, opened):
    with ledger.context(isolation=Isolation.SERIALIZABLE) as unit:
        with unit.context() as same:
            assert same is unit
        with unit.context(isolation=Isolation.SERIALIZABLE) as same:
            assert same is unit
        with pytest.raises(ContractViolation, match="already began"):
            with unit.context(read_only=True):
                pass


# ── what the commit writes ───────────────────────────────────────────────────────────────


class Heard:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str) -> None:
        self.warnings.append(message)


@pytest.fixture
def heard(monkeypatch) -> Heard:
    listening = Heard()
    monkeypatch.setattr(unit_of_work_module, "logger", listening)
    return listening


def test_a_change_never_saved_is_not_written_and_is_named(ledger, opened, heard):
    with ledger.context() as unit:
        account = unit.get(Account, opened.id)
        account.withdraw(30)
        unit.get(Owner, opened.owner_id)  # a read after the change writes nothing

    assert ledger.get(Account, opened.id).balance == 100
    assert any(
        "Account changed inside context() and was never saved" in said
        for said in heard.warnings
    )


def test_what_was_saved_is_written_and_a_change_after_it_is_not(ledger, opened, heard):
    with ledger.context() as unit:
        account = unit.get(Account, opened.id)
        account.withdraw(30)
        unit.save(account)
        account.withdraw(20)

    assert ledger.get(Account, opened.id).balance == 70
    assert heard.warnings


def test_each_commit_of_a_long_unit_of_work_writes_only_what_was_saved(ledger, opened, heard):
    with ledger.context() as unit:
        account = unit.get(Account, opened.id)
        account.withdraw(30)
        unit.commit()
        assert ledger.get(Account, opened.id).balance == 100


def test_writes_changed_makes_the_commit_write_what_the_block_changed(ledger, opened, heard):
    with ledger.context(writes=Writes.CHANGED) as unit:
        account = unit.get(Account, opened.id)
        account.withdraw(30)

    assert ledger.get(Account, opened.id).balance == 70
    assert heard.warnings == []


def test_a_save_writes_only_what_it_names_not_what_changed_beside_it(ledger, opened, heard):
    bob = Owner(name="bob")
    ledger.save(bob)
    with ledger.context() as unit:
        account = unit.get(Account, opened.id)
        owner = unit.get(Owner, bob.id)
        account.withdraw(30)  # changed, never saved
        owner.name = "roberto"
        unit.save(owner)

    assert ledger.get(Account, opened.id).balance == 100
    assert ledger.get(Owner, bob.id).name == "roberto"


def test_a_row_deleted_meanwhile_never_fails_the_commit(ledger, opened, heard):
    with ledger.context() as unit:
        account = unit.get(Account, opened.id)
        account.withdraw(30)
        unit.session.execute(delete(account_table).where(account_table.c.id == opened.id))
        unit.save(Owner(name="still committed"))

    assert ledger.get_by(Owner, name="still committed") is not None


def test_a_nested_block_asking_another_writes_is_refused(ledger):
    with ledger.context(writes=Writes.CHANGED) as unit:
        with unit.context() as same:
            assert same is unit
        with pytest.raises(ContractViolation, match="already began"):
            with unit.context(writes=Writes.SAVED):
                pass


# ── locks ────────────────────────────────────────────────────────────────────────────────


def test_nowait_and_skip_locked_cannot_both_be_asked(ledger, opened):
    with ledger.context() as unit:
        with pytest.raises(ContractViolation, match="ask for one"):
            unit.get(Account, opened.id, for_update=True, nowait=True, skip_locked=True)


def test_how_to_take_a_lock_needs_a_lock(ledger, opened):
    with ledger.context() as unit:
        with pytest.raises(ContractViolation, match="for_update=True"):
            unit.get(Account, opened.id, nowait=True)


def test_nowait_on_a_held_row_stops_at_once_and_is_not_retried(ledger, opened):
    if not on_postgres(ledger):
        pytest.skip("SQLite has no row locks")
    other = Repository(ledger.database)
    attempts = []

    def take() -> None:
        attempts.append(1)
        with other.context() as waiting:
            waiting.get(Account, opened.id, for_update=True, nowait=True)

    with ledger.context() as holder:
        holder.get(Account, opened.id, for_update=True)
        # Another process: on this thread, other would join the unit of work in play.
        with ThreadPoolExecutor(max_workers=1) as elsewhere:
            with pytest.raises(TimedOut, match="told not to wait"):
                elsewhere.submit(other.retrying, take).result()
    assert len(attempts) == 1


def test_a_statement_past_its_timeout_stops(ledger, opened):
    if not on_postgres(ledger):
        pytest.skip("only Postgres bounds a statement")
    with pytest.raises(TimedOut):
        with ledger.context(timeout=0.05) as unit:
            unit.session.execute(text("SELECT pg_sleep(1)"))


def test_the_double_refuses_nowait_as_it_refuses_for_update():
    with pytest.raises(ContractViolation):
        MemoryRepository().get(Account, "x", for_update=True, nowait=True)


# ── a lost race is run again ─────────────────────────────────────────────────────────────


def test_a_serialization_failure_is_a_conflict_and_retrying_wins_it(ledger, opened):
    if not on_postgres(ledger):
        pytest.skip("SQLite serializes writers with a file lock")
    competitor = Repository(ledger.database)
    attempts = []

    def race() -> None:
        with competitor.context() as theirs:
            raced = theirs.get(Account, opened.id)
            assert raced is not None
            raced.withdraw(30)
            theirs.save(raced)

    def withdraw() -> None:
        attempts.append(1)
        with ledger.context(isolation=Isolation.SERIALIZABLE) as unit:
            account = unit.get(Account, opened.id)
            assert account is not None
            if len(attempts) == 1:
                # Another process: on this thread, competitor would join this unit of work.
                with ThreadPoolExecutor(max_workers=1) as elsewhere:
                    elsewhere.submit(race).result()
            account.withdraw(50)
            unit.save(account)

    ledger.retrying(withdraw)

    assert len(attempts) == 2
    assert ledger.get(Account, opened.id).balance == 20
