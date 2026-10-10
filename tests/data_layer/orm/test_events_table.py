"""A bounded context's domain events are entities in one table: saved and found through the
repository like any other, kept with the change that made them true, delivered by a relay, and
— for an entity whose state is its events — rebuilt and guarded by version."""

import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import Column, Integer
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm import (
    Repository,
    map_aggregates,
    map_events,
    template_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.ddd import (
    Condition,
    Criteria,
    DeliverableEventMixin,
    DomainEvent,
    Entity,
    EventSourcedMixin,
)
from sincpro_framework.ddd.criteria import Operator, parse_order
from sincpro_framework.ddd.exceptions import StaleAggregate
from sincpro_framework.entrypoints.adapters.cron import Crons
from sincpro_framework.event_driven import (
    EventRelay,
    FixedBackoff,
    ParkAndContinue,
    Publisher,
    RepositoryQueue,
    RetryInPlace,
    RetryLater,
    SkipAndContinue,
    Subscriber,
    SyncQueue,
)

from .engines import fresh


@dataclass(kw_only=True)
class LedgerEvent(DomainEvent):
    name = "tests.ledger.v1.event"


@dataclass(kw_only=True)
class Paid(DeliverableEventMixin, LedgerEvent):
    name = "tests.ledger.invoice.v1.paid"
    amount: int = 0


@dataclass(kw_only=True)
class DayClosed(LedgerEvent):
    name = "tests.ledger.v1.day_closed"
    closed_by: str = ""


@dataclass(kw_only=True)
class Deposited(LedgerEvent):
    name = "tests.ledger.account.v1.deposited"
    amount: int = 0


@dataclass
class Bill(Entity):
    total: int = 0

    def pay(self, amount: int) -> None:
        self.total -= amount
        self.record(Paid(amount=amount))


@dataclass
class Wallet(EventSourcedMixin, Entity):
    event_base = LedgerEvent
    balance: int = 0

    def apply(self, event: DomainEvent) -> None:
        if isinstance(event, Deposited):
            self.balance += event.amount

    def deposit(self, amount: int) -> None:
        self.happened(Deposited(amount=amount))


kept = registry()
bill_table = entity_table("et_bill", kept.metadata, Column("total", Integer, nullable=False))
ledger_events = template_table.event_table("et_ledger_events", kept.metadata)
map_aggregates(kept, {Bill: bill_table})
map_events(kept, LedgerEvent, ledger_events)


class Broker:
    def __init__(self) -> None:
        self.sent: list[DomainEvent] = []
        self.down = False

    def publish(self, event: DomainEvent) -> None:
        if self.down:
            raise ConnectionError("the broker is down")
        self.sent.append(event)


@pytest.fixture
def repository(engine_url: str) -> Repository:
    return Repository(fresh(engine_url, kept.metadata))


def on_sqlite(repository: Repository) -> bool:
    return repository.database.engine.dialect.name == "sqlite"


def a_paid_bill(repository: Repository, *amounts: int) -> Bill:
    bill = Bill(total=100)
    repository.save(bill)
    for amount in amounts:
        with repository.context() as unit:
            again = unit.get(Bill, bill.id)
            assert again is not None
            again.pay(amount)
            unit.save(again)
    return bill


# ── an event is an entity ───────────────────────────────────────────────────────────────


def test_events_are_saved_and_found_through_the_repository_as_their_classes(repository):
    repository.save([DayClosed(closed_by="ana"), Paid(amount=10)])

    every = repository.fetch_all(LedgerEvent, Criteria(order=parse_order("id"))).items
    assert [type(one) for one in every] == [DayClosed, Paid]
    [closed] = repository.fetch_all(DayClosed).items
    assert closed.closed_by == "ana"  # what its class adds, typed, back from the payload
    assert repository.get(Paid, every[1].id).amount == 10


def test_an_event_about_no_entity_is_kept_like_any_other(repository):
    repository.save([DayClosed()])
    [closed] = repository.fetch_all(DayClosed).items
    assert (closed.entity_type, closed.entity_id, closed.entity_version) == ("", "", None)


def test_an_entity_history_is_its_events_filtered_by_the_envelope(repository):
    bill = a_paid_bill(repository, 10, 20)
    a_paid_bill(repository, 5)

    about = Criteria(
        where=Condition(field="entity_id", value=bill.id), order=parse_order("id")
    )
    assert [one.amount for one in repository.fetch_all(Paid, about).items] == [10, 20]


def test_an_event_class_declared_after_its_base_was_mapped_is_kept_too(repository):
    @dataclass(kw_only=True)
    class Reopened(LedgerEvent):
        name = "tests.ledger.v1.reopened"
        reason: str = ""

    repository.save([Reopened(reason="late payment")])
    [reopened] = repository.fetch_all(Reopened).items
    assert reopened.reason == "late payment"


# ── kept with the change ───────────────────────────────────────────────────────────────


def test_saving_an_aggregate_keeps_what_it_recorded_in_the_same_commit(repository):
    bill = a_paid_bill(repository)
    with pytest.raises(RuntimeError):
        with repository.context() as unit:
            again = unit.get(Bill, bill.id)
            again.pay(10)
            unit.save(again)
            raise RuntimeError("the payment gateway is down")

    assert repository.count(Paid).value == 0
    a_paid_bill(repository, 30)
    assert repository.count(Paid).value == 1


def test_the_aggregate_keeps_its_events_for_whoever_pulls_them(repository):
    bill = Bill(total=100)
    repository.save(bill)
    with repository.context() as unit:
        again = unit.get(Bill, bill.id)
        again.pay(30)
        unit.save(again)
        unit.save(again)  # the same event, kept once
        pulled = again.pull_events()

    assert [type(one) for one in pulled] == [Paid]
    assert repository.count(Paid).value == 1


def test_publishing_through_the_repository_keeps_the_event(repository):
    with repository.context():
        Publisher(RepositoryQueue(repository)).publish(DayClosed())
    assert repository.count(DayClosed).value == 1


# ── deliverable ───────────────────────────────────────────────────────────────────────


def test_a_deliverable_event_is_due_at_once_and_what_it_sends_leaves_its_delivery_out(
    repository,
):
    repository.save([Paid(amount=1), DayClosed()])
    deliverable = Criteria(where=Condition(field="is_deliverable", value=True))

    assert [type(one) for one in repository.fetch_all(LedgerEvent, deliverable).items] == [
        Paid
    ]
    [paid] = repository.fetch_all(Paid).items
    assert paid.next_delivery_at is not None and paid.delivered_at is None
    assert "delivered_at" not in paid.as_json() and "delivery" not in paid.as_json()


def test_a_deliverable_class_without_a_versioned_name_is_warned_about():
    with warnings.catch_warnings(record=True) as heard:
        warnings.simplefilter("always")

        @dataclass(kw_only=True)
        class Unversioned(DeliverableEventMixin, DomainEvent):
            pass

    assert any("versioned name" in str(one.message) for one in heard)


# ── the relay ─────────────────────────────────────────────────────────────────────────


def test_the_relay_delivers_each_event_once_and_marks_it(repository):
    a_paid_bill(repository, 10, 20)
    broker = Broker()
    relay = EventRelay(repository, LedgerEvent, broker)

    first = relay.run_once()
    assert (first.read, first.delivered) == (2, 2)
    assert relay.run_once().read == 0
    assert [getattr(one, "amount") for one in broker.sent] == [10, 20]
    undelivered = Criteria(
        where=Condition(field="delivered_at", operator=Operator.IS_NULL, value=True)
    )
    assert repository.count(Paid, undelivered).value == 0


def test_a_failed_delivery_waits_its_backoff_then_parks(repository):
    a_paid_bill(repository, 10)
    broker = Broker()
    broker.down = True
    policy = RetryInPlace(
        attempts=2, backoff=FixedBackoff(timedelta(0)), then=ParkAndContinue()
    )
    relay = EventRelay(repository, LedgerEvent, broker, policy)

    assert relay.run_once().retried == 1
    assert relay.run_once().parked == 1
    assert relay.run_once().read == 0  # parked: never tried again
    [paid] = repository.fetch_all(Paid).items
    assert paid.delivery["parked"] and "ConnectionError" in paid.delivery["last_error"]


def test_a_retry_later_holds_the_later_events_of_its_entity(repository):
    bill = a_paid_bill(repository, 10, 20)
    other = a_paid_bill(repository, 5)

    class DownForTheFirstBill(Broker):
        failing = True

        def publish(self, event: DomainEvent) -> None:
            if event.entity_id == bill.id and getattr(event, "amount") == 10 and self.failing:
                raise ConnectionError("down for 10")
            super().publish(event)

    broker = DownForTheFirstBill()
    relay = EventRelay(
        repository,
        LedgerEvent,
        broker,
        RetryLater(attempts=3, backoff=FixedBackoff(timedelta(0))),
    )

    done = relay.run_once()
    assert (done.retried, done.held, done.delivered) == (1, 1, 1)
    assert [one.entity_id for one in broker.sent] == [other.id]
    broker.failing = False
    relay.run_once()
    assert [getattr(one, "amount") for one in broker.sent if one.entity_id == bill.id] == [
        10,
        20,
    ]


def test_a_destination_that_can_lose_one_skips_it(repository):
    a_paid_bill(repository, 10)
    broker = Broker()
    broker.down = True
    done = EventRelay(repository, LedgerEvent, broker, SkipAndContinue()).run_once()
    assert done.skipped == 1
    assert EventRelay(repository, LedgerEvent, Broker()).run_once().read == 0


def test_replicas_running_one_relay_never_deliver_an_event_twice(repository):
    if on_sqlite(repository):
        pytest.skip("an in-memory SQLite has one connection: there is no second replica")
    repository.save([Paid(amount=n) for n in range(1, 121)])
    delivered: list[int] = []

    class Collect:
        def publish(self, event: DomainEvent) -> None:
            delivered.append(event.amount)  # type: ignore[attr-defined]

    def replica() -> None:
        relay = EventRelay(repository, LedgerEvent, Collect(), batch=7)
        for _ in range(200):
            if relay.run_once().read == 0:
                return

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: replica(), range(4)))

    assert sorted(delivered) == list(range(1, 121))


def test_a_cron_drives_the_relay(repository):
    crons = Crons("delivery")
    relay = crons.relay_deliverable_events(
        repository=repository, source=LedgerEvent, to=Publisher(SyncQueue(Subscriber()))
    )
    assert relay.source is LedgerEvent
    assert [one.name for one in crons.definitions] == ["delivery.relay"]


# ── event sourced ───────────────────────────────────────────────────────────────────────


def test_an_event_sourced_entity_is_rebuilt_from_its_events(repository):
    wallet = Wallet(id="W-1")
    wallet.deposit(10)
    wallet.deposit(5)
    repository.save(wallet)

    again = repository.get(Wallet, "W-1")
    assert (again.balance, again.version) == (15, 2)
    assert repository.get(Wallet, "nobody") is None


def test_two_writers_of_one_event_sourced_entity_do_not_both_win(repository):
    wallet = Wallet(id="W-1")
    wallet.deposit(10)
    repository.save(wallet)
    first, second = repository.get(Wallet, "W-1"), repository.get(Wallet, "W-1")

    first.deposit(1)
    repository.save(first)
    second.deposit(2)
    with pytest.raises(StaleAggregate):
        repository.save(second)
    assert repository.get(Wallet, "W-1").balance == 11
