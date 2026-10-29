"""A bounded context's domain events are entities in one table: saved and found through the
repository like any other, kept with the change that made them true, published once it committed
and stamped with how their delivery went, and — for an entity whose state is its events — rebuilt
and guarded by version."""

import threading
import time
import warnings
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
    exceptions,
)
from sincpro_framework.ddd.criteria import (
    Sort,
)
from sincpro_framework.event_driven import (
    Forwarded,
    Forwarding,
    ForwardingHook,
    Publisher,
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


@dataclass(kw_only=True)
class Reopened(LedgerEvent):
    """Declared after its base was mapped: the first save maps it. Once, at module level — a
    class declared inside a test is a new class on every run (one per engine), and two classes
    under one name are refused, as they must be."""

    name = "tests.ledger.v1.reopened"
    reason: str = ""


class Broker:
    def __init__(self) -> None:
        self.sent: list[DomainEvent] = []
        self.down = False

    def publish(self, event: DomainEvent) -> None:
        if self.down:
            raise ConnectionError("the broker is down")
        self.sent.append(event)


class BrokerQueue:
    """The broker as a queue, for a publisher that forwards to it."""

    def __init__(self, broker: Broker) -> None:
        self.broker = broker

    def put(self, message: DomainEvent) -> None:
        self.broker.publish(message)

    async def aput(self, message: DomainEvent) -> None:
        self.broker.publish(message)


@pytest.fixture
def repository(engine_url: str) -> Repository:
    return Repository(fresh(engine_url, kept.metadata))


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

    every = repository.fetch_all(LedgerEvent, Criteria(order=Sort.parse("id"))).items
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
        where=Condition(field="entity_id", value=bill.id), order=Sort.parse("id")
    )
    assert [one.amount for one in repository.fetch_all(Paid, about).items] == [10, 20]


def test_an_event_class_declared_after_its_base_was_mapped_is_kept_too(repository):
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


# ── deliverable ───────────────────────────────────────────────────────────────────────


def test_what_a_deliverable_event_sends_leaves_its_delivery_out(repository):
    repository.save([Paid(amount=1)])

    [paid] = repository.fetch_all(Paid).items
    assert paid.delivered_at is None
    assert "delivered_at" not in paid.as_json() and "delivery" not in paid.as_json()


def test_a_deliverable_class_without_a_versioned_name_is_warned_about():
    with warnings.catch_warnings(record=True) as heard:
        warnings.simplefilter("always")

        @dataclass(kw_only=True)
        class Unversioned(DeliverableEventMixin, DomainEvent):
            pass

    assert any("versioned name" in str(one.message) for one in heard)


# ── published on commit ─────────────────────────────────────────────────────────────────


def test_what_a_write_recorded_goes_out_after_the_commit_and_its_row_says_when(engine_url):
    broker = Broker()
    repository = Repository(fresh(engine_url, kept.metadata), publish_on_commit=broker)

    with repository.context() as unit:
        bill = Bill(total=100)
        bill.pay(30)
        unit.save(bill)
        assert broker.sent == []

    assert [getattr(one, "amount") for one in broker.sent] == [30]
    [paid] = repository.fetch_all(Paid).items
    assert paid.delivered_at is not None and paid.delivery_attempts == 1


def test_a_row_says_why_its_event_did_not_go_out(engine_url):
    broker = Broker()
    broker.down = True
    repository = Repository(fresh(engine_url, kept.metadata), publish_on_commit=broker)
    bill = Bill(total=100)
    bill.pay(30)

    repository.save(bill)

    [paid] = repository.fetch_all(Paid).items
    assert paid.delivered_at is None
    assert "the broker is down" in paid.delivery["last_error"]


def test_a_forwarded_event_s_row_is_stamped_once_the_broker_took_it(engine_url):
    if engine_url.startswith("sqlite"):
        pytest.skip(
            "an in-memory SQLite is another empty database on the forwarding's thread"
        )

    class FirstFailure(ForwardingHook):
        def __init__(self) -> None:
            self.seen = threading.Event()

        def after_failure(self, message: DomainEvent, forwarded: Forwarded) -> None:
            self.seen.set()

    broker = Broker()
    broker.down = True
    failed = FirstFailure()
    quickly = timedelta(milliseconds=5)
    publisher = Publisher(
        BrokerQueue(broker),
        forwarding=Forwarding(hooks=[failed], wait=quickly, longest_wait=quickly),
    )
    repository = Repository(fresh(engine_url, kept.metadata), publish_on_commit=publisher)
    bill = Bill(total=100)
    bill.pay(30)

    repository.save(bill)
    assert repository.fetch_all(Paid).items[0].delivered_at is None
    assert failed.seen.wait(5)
    broker.down = False

    deadline = time.monotonic() + 5
    while repository.fetch_all(Paid).items[0].delivered_at is None:
        assert time.monotonic() < deadline, "the forwarded event was never stamped"
        time.sleep(0.01)
    assert repository.fetch_all(Paid).items[0].delivery_attempts > 1


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
    with pytest.raises(exceptions.StaleAggregate):
        repository.save(second)
    assert repository.get(Wallet, "W-1").balance == 11
