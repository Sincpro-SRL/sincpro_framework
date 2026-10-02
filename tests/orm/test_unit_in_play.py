"""A unit of work opened in an ApplicationService is the one every Feature it calls writes in:
several flows validated together, committed together — or none of them."""

import pytest

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    UseFramework,
)
from sincpro_framework.ddd.exceptions import ConstraintViolation
from sincpro_framework.orm import Repository

from .engines import fresh
from .ledger_models import Account, Owner, bank, declare


class CommandOpen(DataTransferObject):
    name: str
    balance: int


class ResponseOpen(DataTransferObject):
    account_id: str


class CommandWithdraw(DataTransferObject):
    account_id: str
    amount: int


class ResponseWithdraw(DataTransferObject):
    balance: int


class CommandOpenAndWithdraw(DataTransferObject):
    name: str
    balance: int
    amount: int
    fail_after: bool = False


class ResponseOpenAndWithdraw(DataTransferObject):
    account_id: str


def ledger_bus(repository: Repository) -> UseFramework:
    bus = UseFramework("ledger", log_after_execution=False)
    bus.add_dependency("repository", repository)

    @bus.feature(CommandOpen)
    class Open(Feature):
        repository: Repository

        def execute(self, dto: CommandOpen) -> ResponseOpen:
            owner = Owner(name=dto.name)
            self.repository.save(owner)
            account = Account(owner_id=owner.id, balance=dto.balance)
            self.repository.save(account)
            return ResponseOpen(account_id=account.id)

    @bus.feature(CommandWithdraw)
    class Withdraw(Feature):
        repository: Repository

        def execute(self, dto: CommandWithdraw) -> ResponseWithdraw:
            account = self.repository.get(Account, dto.account_id, for_update=True)
            assert account is not None
            account.withdraw(dto.amount)
            self.repository.save(account)
            return ResponseWithdraw(balance=account.balance)

    @bus.app_service(CommandOpenAndWithdraw)
    class OpenAndWithdraw(ApplicationService):
        repository: Repository

        def execute(self, dto: CommandOpenAndWithdraw) -> ResponseOpenAndWithdraw:
            with self.repository.context():
                opened = self.feature_bus(
                    CommandOpen(name=dto.name, balance=dto.balance), ResponseOpen
                )
                self.feature_bus(
                    CommandWithdraw(account_id=opened.account_id, amount=dto.amount),
                    ResponseWithdraw,
                )
                if dto.fail_after:
                    raise RuntimeError("the second check failed")
            return ResponseOpenAndWithdraw(account_id=opened.account_id)

    return bus


@pytest.fixture
def ledger(engine_url: str) -> Repository:
    declare()
    return Repository(fresh(engine_url, bank.metadata, enforce_foreign_keys=True))


def test_two_features_in_one_unit_of_work_commit_together(ledger):
    bus = ledger_bus(ledger)

    done = bus(
        CommandOpenAndWithdraw(name="ana", balance=100, amount=30), ResponseOpenAndWithdraw
    )

    account = ledger.get(Account, done.account_id)
    assert account is not None and account.balance == 70


def test_a_failure_after_both_features_undoes_both(ledger):
    bus = ledger_bus(ledger)

    with pytest.raises(RuntimeError, match="second check"):
        bus(
            CommandOpenAndWithdraw(name="ana", balance=100, amount=30, fail_after=True),
            ResponseOpenAndWithdraw,
        )

    assert ledger.count(Owner).value == 0
    assert ledger.count(Account).value == 0


def test_the_second_feature_refused_by_the_database_undoes_the_first(ledger):
    bus = ledger_bus(ledger)

    with pytest.raises(ConstraintViolation):
        bus(
            CommandOpenAndWithdraw(name="ana", balance=10, amount=30), ResponseOpenAndWithdraw
        )

    assert ledger.count(Owner).value == 0


def test_a_feature_called_on_its_own_still_commits_on_its_own(ledger):
    bus = ledger_bus(ledger)

    opened = bus(CommandOpen(name="ana", balance=5), ResponseOpen)

    assert ledger.get(Account, opened.account_id) is not None


def test_a_repository_on_another_database_never_joins(ledger, engine_url):
    other = Repository(fresh("sqlite://", bank.metadata))
    with pytest.raises(RuntimeError):
        with ledger.context():
            other.save(Owner(name="bea"))
            raise RuntimeError("undo")

    assert other.count(Owner).value == 1


def test_what_waits_for_the_commit_writes_in_a_unit_of_work_of_its_own(ledger):
    with ledger.context() as unit:
        unit.after_commit(lambda: ledger.save(Owner(name="after")))
        unit.save(Owner(name="before"))

    assert sorted(ledger.pluck(Owner, "name")) == ["after", "before"]


def test_a_separate_unit_of_work_outlives_the_rollback_around_it(ledger):
    if ledger.database.engine.dialect.name == "sqlite":
        pytest.skip(
            "an in-memory SQLite has one connection: two transactions cannot share it"
        )
    with pytest.raises(RuntimeError):
        with ledger.context():
            ledger.save(Owner(name="attempt"))
            with ledger.context(separate=True) as audit:
                audit.save(Owner(name="audit"))
            raise RuntimeError("undo")

    assert ledger.pluck(Owner, "name") == ["audit"]
