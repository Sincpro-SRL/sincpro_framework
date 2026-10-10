"""Writes that go past the aggregate on purpose — an upsert, a write by criteria — keep what a
later write needs to stay safe, and never run wider than they were asked; and what waits for the
commit waits for this unit of work's."""

import pytest

from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure import transaction_hooks
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd.criteria import Condition, Criteria, Operator
from sincpro_framework.ddd.exceptions import (
    ConstraintViolation,
    ContractViolation,
    DuplicateAggregate,
    StaleAggregate,
)
from sincpro_framework.ddd.repositories.hooks import Hook, Hooks

from .engines import fresh
from .ledger_models import Account, Owner, bank, declare


@pytest.fixture
def ledger(engine_url: str) -> Repository:
    declare()
    return Repository(fresh(engine_url, bank.metadata, enforce_foreign_keys=True))


@pytest.fixture
def ana(ledger: Repository) -> Owner:
    owner = Owner(name="ana")
    ledger.save(owner)
    return owner


def balances(repository: Repository) -> list[int]:
    return sorted(repository.pluck(Account, "balance"))


def owned_by(owner: Owner) -> Criteria:
    return Criteria(where=Condition(field="owner_id", value=owner.id))


# ── upsert ───────────────────────────────────────────────────────────────────────────────


def test_an_upsert_inserts_what_is_new_and_overwrites_what_holds_the_key(ledger, ana):
    held = Account(owner_id=ana.id, balance=10)
    ledger.save(held)

    ledger.upsert(
        [
            Account(id=held.id, owner_id=ana.id, balance=20),
            Account(owner_id=ana.id, balance=5),
        ],
        on=("id",),
    )

    assert balances(ledger) == [5, 20]
    assert ledger.get(Account, held.id).version == 2


def test_a_conflict_on_a_record_that_is_only_its_key_changes_nothing(ledger, ana):
    ledger.upsert([Owner(name="ana"), Owner(name="bob")], on=("name",))

    assert sorted(ledger.pluck(Owner, "name")) == ["ana", "bob"]
    stored = ledger.get_by(Owner, name="ana")
    assert stored.id == ana.id and stored.version == 1


def test_an_upsert_of_the_same_key_twice_in_one_batch_keeps_the_last(ledger, ana):
    first = Account(owner_id=ana.id, balance=1)
    last = Account(id=first.id, owner_id=ana.id, balance=2)

    ledger.upsert([first, last], on=("id",))

    assert balances(ledger) == [2]


def test_an_upsert_with_nothing_to_update_leaves_the_stored_row(ledger, ana):
    account = Account(owner_id=ana.id, balance=10)
    ledger.save(account)

    ledger.upsert(Account(id=account.id, owner_id=ana.id, balance=99), on=("id",), update=())

    assert ledger.get(Account, account.id).balance == 10


def test_an_upsert_overwrites_only_what_it_names(ledger, ana):
    account = Account(owner_id=ana.id, balance=10)
    ledger.save(account)
    bob = Owner(name="bob")
    ledger.save(bob)

    ledger.upsert(
        Account(id=account.id, owner_id=bob.id, balance=99), on=("id",), update=("balance",)
    )

    stored = ledger.get(Account, account.id)
    assert (stored.balance, stored.owner_id) == (99, ana.id)


def test_an_upsert_answers_how_many_it_wrote_and_how_many_it_left_as_they_were(ledger, ana):
    held = Account(owner_id=ana.id, balance=10)
    ledger.save(held)

    overwrite = ledger.upsert(
        [
            Account(id=held.id, owner_id=ana.id, balance=20),
            Account(owner_id=ana.id, balance=5),
        ],
        on=("id",),
    )
    leave = ledger.upsert(
        Account(id=held.id, owner_id=ana.id, balance=99), on=("id",), update=()
    )

    assert (overwrite.written, overwrite.skipped) == (2, 0)
    assert (leave.written, leave.skipped) == (0, 1)


def test_a_row_outside_the_scope_is_counted_as_skipped(ledger, ana):
    bob = Owner(name="bob")
    ledger.save(bob)
    theirs = Account(owner_id=bob.id, balance=500)
    ledger.save(theirs)

    done = ledger.narrowed(owned_by(ana)).upsert(
        Account(id=theirs.id, owner_id=ana.id, balance=0), on=("id",)
    )

    assert (done.written, done.skipped) == (0, 1)


def test_the_double_counts_as_the_engine_does():
    ana = Owner(name="ana")
    held = Account(owner_id=ana.id, balance=10)
    double = MemoryRepository(ana, held)

    done = double.upsert(
        [
            Account(id=held.id, owner_id=ana.id, balance=20),
            Account(owner_id=ana.id, balance=5),
        ],
        on=("id",),
    )
    left = double.upsert(
        Account(id=held.id, owner_id=ana.id, balance=1), on=("id",), update=()
    )

    assert (done.written, done.skipped, left.written, left.skipped) == (2, 0, 0, 1)


def test_an_upsert_on_a_key_the_table_does_not_hold_unique_is_refused(ledger, ana):
    with pytest.raises(ContractViolation, match="unique"):
        ledger.upsert(Account(owner_id=ana.id, balance=1), on=("balance",))


def test_a_narrowed_upsert_never_overwrites_a_row_outside_its_scope(ledger, ana):
    bob = Owner(name="bob")
    ledger.save(bob)
    theirs = Account(owner_id=bob.id, balance=500)
    ledger.save(theirs)
    mine = ledger.narrowed(owned_by(ana))

    mine.upsert(Account(id=theirs.id, owner_id=ana.id, balance=0), on=("id",))

    stored = ledger.get(Account, theirs.id)
    assert (stored.owner_id, stored.balance) == (bob.id, 500)


def test_an_upsert_cannot_overwrite_what_the_framework_keeps(ledger, ana):
    with pytest.raises(ContractViolation, match="the framework keeps"):
        ledger.upsert(Owner(name="ana"), on=("name",), update=("id",))
    with pytest.raises(ContractViolation, match="the framework keeps"):
        MemoryRepository().upsert(Owner(name="ana"), on=("name",), update=("version",))


def test_an_upsert_of_a_stored_identity_under_another_key_is_a_duplicate_in_both(ledger, ana):
    with pytest.raises(DuplicateAggregate):
        ledger.upsert(Owner(id=ana.id, name="someone else"), on=("name",))
    double = MemoryRepository(Owner(id=ana.id, name="ana"))
    with pytest.raises(DuplicateAggregate):
        double.upsert(Owner(id=ana.id, name="someone else"), on=("name",))


def test_an_upsert_runs_the_save_hooks(engine_url):
    seen: list[str] = []

    hooks = Hooks(None)

    @hooks.on(Owner)
    class Seen(Hook):
        def before_save(self, record: Owner) -> None:
            seen.append(f"before {record.name}")

        def after_save(self, record: Owner) -> None:
            seen.append(f"after {record.name}")

    declare()
    repository = Repository(fresh(engine_url, bank.metadata), hooks)

    repository.upsert(Owner(name="ana"), on=("name",))

    assert seen == ["before ana", "after ana"]


# ── writes by criteria ───────────────────────────────────────────────────────────────────


def test_update_all_sets_every_matching_row_and_answers_how_many(ledger, ana):
    ledger.save([Account(owner_id=ana.id, balance=b) for b in (1, 2, 3)])

    changed = ledger.update_all(
        Account,
        Criteria(where=Condition(field="balance", operator=Operator.GTE, value=2)),
        {"balance": 0},
    )

    assert changed == 2 and balances(ledger) == [0, 0, 1]


def test_update_all_raises_the_version_so_a_stale_copy_is_still_refused(ledger, ana):
    account = Account(owner_id=ana.id, balance=5)
    ledger.save(account)
    stale = ledger.get(Account, account.id)

    ledger.update_all(Account, owned_by(ana), {"balance": 6})

    stale.balance = 7
    with pytest.raises(StaleAggregate):
        ledger.save(stale)


def test_a_write_by_criteria_never_runs_wider_than_its_scope(ledger, ana):
    bob = Owner(name="bob")
    ledger.save(bob)
    ledger.save([Account(owner_id=ana.id, balance=1), Account(owner_id=bob.id, balance=1)])

    changed = ledger.narrowed(owned_by(ana)).update_all(Account, Criteria(), {"balance": 9})

    assert changed == 1 and balances(ledger) == [1, 9]


def test_a_write_by_criteria_refuses_a_page(ledger, ana):
    with pytest.raises(ContractViolation, match="page"):
        ledger.update_all(
            Account, Criteria.model_validate({"pagination": {"limit": 10}}), {"balance": 0}
        )


def test_a_write_by_criteria_refuses_a_condition_it_cannot_answer(ledger, ana):
    unknown = Criteria(where=Condition(field="nothing_like_this", value=1))
    with pytest.raises(ContractViolation, match="wider"):
        ledger.remove_all(Account, unknown)


def test_update_all_refuses_the_fields_the_framework_keeps(ledger, ana):
    with pytest.raises(ContractViolation, match="version"):
        ledger.update_all(Account, Criteria(), {"version": 1})


def test_remove_all_deletes_every_matching_row(ledger, ana):
    ledger.save([Account(owner_id=ana.id, balance=b) for b in (1, 2)])

    assert ledger.remove_all(Account, owned_by(ana)) == 2
    assert balances(ledger) == []


def test_remove_all_is_refused_by_a_row_still_pointing_at_it(ledger, ana):
    ledger.save(Account(owner_id=ana.id, balance=1))
    with pytest.raises(ConstraintViolation):
        ledger.remove_all(Owner, Criteria())


def test_a_read_only_unit_of_work_refuses_the_bulk_writes_too(ledger, ana):
    with ledger.context(read_only=True) as unit:
        with pytest.raises(ContractViolation, match="read_only"):
            unit.update_all(Account, Criteria(), {"balance": 0})
        with pytest.raises(ContractViolation, match="read_only"):
            unit.upsert(Owner(name="x"), on=("name",))


# ── what waits for the commit ────────────────────────────────────────────────────────────


def test_after_commit_runs_once_the_unit_of_work_committed(ledger):
    heard: list[str] = []
    with ledger.context() as unit:
        unit.save(Owner(name="ana"))
        unit.after_commit(lambda: heard.append(f"committed: {ledger.count(Owner).value}"))
        assert heard == []
    assert heard == ["committed: 1"]


def test_a_rollback_runs_after_rollback_and_never_after_commit(ledger):
    heard: list[str] = []
    with pytest.raises(RuntimeError):
        with ledger.context() as unit:
            unit.after_commit(lambda: heard.append("committed"))
            unit.after_rollback(lambda: heard.append("undone"))
            raise RuntimeError("the gateway is down")
    assert heard == ["undone"]


def test_a_savepoint_that_is_undone_drops_what_it_promised(ledger):
    heard: list[str] = []
    with ledger.context() as unit:
        unit.after_commit(lambda: heard.append("outer"))
        with pytest.raises(RuntimeError):
            with unit.savepoint():
                unit.after_commit(lambda: heard.append("inner, undone"))
                unit.after_rollback(lambda: heard.append("inner rolled back"))
                raise RuntimeError
        with unit.savepoint():
            unit.after_commit(lambda: heard.append("inner, kept"))
    assert heard == ["inner rolled back", "outer", "inner, kept"]


def test_each_commit_of_a_long_unit_of_work_runs_what_came_before_it(ledger):
    heard: list[str] = []
    with ledger.context() as unit:
        unit.after_commit(lambda: heard.append("first batch"))
        unit.commit()
        assert heard == ["first batch"]
        unit.after_commit(lambda: heard.append("second batch"))
    assert heard == ["first batch", "second batch"]


def test_a_callback_that_raises_does_not_stop_the_rest(ledger, monkeypatch):
    heard: list[str] = []
    logged: list[str] = []

    class Heard:
        def exception(self, message: str) -> None:
            logged.append(message)

    monkeypatch.setattr(transaction_hooks, "logger", Heard())

    def failing() -> None:
        raise ValueError("the broker is down")

    with ledger.context() as unit:
        unit.after_commit(failing)
        unit.after_commit(lambda: heard.append("still ran"))
    assert heard == ["still ran"] and len(logged) == 1


def test_after_commit_outside_a_unit_of_work_is_refused(ledger):
    with pytest.raises(ContractViolation, match="unit of work"):
        ledger.after_commit(lambda: None)


# ── the double answers the same ──────────────────────────────────────────────────────────


def test_the_double_upserts_updates_and_removes_by_criteria_as_the_engine_does():
    ana = Owner(name="ana")
    double = MemoryRepository()
    double.save(ana)

    double.upsert([Owner(name="ana"), Owner(name="bob")], on=("name",))
    assert sorted(double.pluck(Owner, "name")) == ["ana", "bob"]
    stored = double.get(Owner, ana.id)
    assert stored is not None and stored.version == 1

    double.save([Account(owner_id=ana.id, balance=b) for b in (1, 2)])
    assert double.update_all(Account, owned_by(ana), {"balance": 0}) == 2
    assert double.remove_all(Account, owned_by(ana)) == 2
    with pytest.raises(ContractViolation, match="page"):
        double.remove_all(Account, Criteria.model_validate({"pagination": {"limit": 1}}))


def test_records_whose_key_holds_a_null_are_each_their_own(ledger, ana):
    done = ledger.upsert(
        [Account(owner_id=ana.id, balance=1), Account(owner_id=ana.id, balance=2)],
        on=("reference",),
    )

    assert (done.written, done.skipped) == (2, 0) and balances(ledger) == [1, 2]


def test_an_upsert_never_brings_an_archived_row_back(ledger, ana):
    ledger.archive(ana)

    ledger.upsert(Owner(name="ana"), on=("name",))

    archived = Criteria.model_validate(
        {"where": {"field": "archived_at", "operator": "is null", "value": False}}
    )
    assert [owner.name for owner in ledger.fetch_all(Owner, archived).items] == ["ana"]


def test_the_double_refuses_an_unknown_column_as_the_engine_does():
    with pytest.raises(ContractViolation, match="no field"):
        MemoryRepository().upsert(Owner(name="ana"), on=("name",), update=("colour",))
