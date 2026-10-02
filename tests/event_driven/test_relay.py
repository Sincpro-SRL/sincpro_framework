"""The relay runs over any repository: on the in-memory double it delivers, retries, parks and
keeps an entity's order the same way it does on a database."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from sincpro_framework.ddd import (
    DeliverableEventMixin,
    DomainEvent,
    Entity,
    EventSourcedMixin,
)
from sincpro_framework.ddd.exceptions import ContractViolation, StaleAggregate
from sincpro_framework.ddd.repositories import MemoryRepository
from sincpro_framework.event_driven import (
    EventRelay,
    FixedBackoff,
    ParkAndContinue,
    Publisher,
    RepositoryQueue,
    RetryInPlace,
    RetryLater,
)


@dataclass(kw_only=True)
class ShopEvent(DomainEvent):
    name = "tests.relay.v1.event"


@dataclass(kw_only=True)
class OrderShipped(DeliverableEventMixin, ShopEvent):
    name = "tests.relay.order.v1.shipped"
    carrier: str = ""


@dataclass(kw_only=True)
class StockCounted(ShopEvent):
    name = "tests.relay.v1.stock_counted"


@dataclass(kw_only=True)
class Credited(ShopEvent):
    name = "tests.relay.wallet.v1.credited"
    amount: int = 0


@dataclass
class Wallet(EventSourcedMixin, Entity):
    event_base = ShopEvent
    balance: int = 0

    def apply(self, event: DomainEvent) -> None:
        if isinstance(event, Credited):
            self.balance += event.amount

    def credit(self, amount: int) -> None:
        self.happened(Credited(amount=amount))


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2030, 1, 1, 12, 0, 0).astimezone()

    def __call__(self) -> datetime:
        return self.now


class Broker:
    def __init__(self) -> None:
        self.sent: list[DomainEvent] = []
        self.failing: set[str] = set()

    def publish(self, event: DomainEvent) -> None:
        if event.entity_id in self.failing:
            raise ConnectionError("the broker is down")
        self.sent.append(event)


def shipped(order: str, carrier: str = "dhl") -> OrderShipped:
    return OrderShipped(entity_type="Order", entity_id=order, carrier=carrier)


@pytest.fixture
def repository() -> MemoryRepository:
    return MemoryRepository()


def test_only_deliverable_events_go_out_once_each(repository):
    repository.save([shipped("O-1"), StockCounted(), shipped("O-2")])
    broker = Broker()
    relay = EventRelay(repository, ShopEvent, broker, clock=Clock())

    assert relay.run_once().delivered == 2
    assert relay.run_once().read == 0
    assert [one.entity_id for one in broker.sent] == ["O-1", "O-2"]


def test_a_retry_in_place_holds_everything_behind_it_then_parks(repository):
    repository.save([shipped("O-1"), shipped("O-2")])
    broker = Broker()
    broker.failing = {"O-1"}
    policy = RetryInPlace(
        attempts=2, backoff=FixedBackoff(timedelta(0)), then=ParkAndContinue()
    )
    relay = EventRelay(repository, ShopEvent, broker, policy, clock=Clock())

    assert (relay.run_once().retried, broker.sent) == (1, [])  # O-2 waited behind O-1
    second = relay.run_once()
    assert (second.parked, second.delivered) == (1, 1)
    assert [one.entity_id for one in broker.sent] == ["O-2"]


def test_a_retry_later_lets_other_entities_go_and_keeps_its_own_order(repository):
    repository.save([shipped("O-1", "dhl"), shipped("O-2"), shipped("O-1", "ups")])
    broker = Broker()
    broker.failing = {"O-1"}
    clock = Clock()
    policy = RetryLater(attempts=3, backoff=FixedBackoff(timedelta(seconds=10)))
    relay = EventRelay(repository, ShopEvent, broker, policy, clock=clock)

    first = relay.run_once()
    assert (first.retried, first.held, first.delivered) == (1, 1, 1)
    broker.failing = set()
    clock.now += timedelta(seconds=10)
    relay.run_once()
    assert [getattr(one, "carrier") for one in broker.sent if one.entity_id == "O-1"] == [
        "dhl",
        "ups",
    ]


def test_a_parked_event_replayed_goes_out(repository):
    repository.save([shipped("O-1")])
    broker = Broker()
    broker.failing = {"O-1"}
    EventRelay(repository, ShopEvent, broker, ParkAndContinue(), clock=Clock()).run_once()
    [parked] = repository.fetch_all(OrderShipped).items
    assert parked.delivery["parked"]

    broker.failing = set()
    parked.replayed(Clock()())
    repository.save(parked)
    assert EventRelay(repository, ShopEvent, broker, clock=Clock()).run_once().delivered == 1


def test_publishing_through_the_repository_keeps_the_event_for_the_relay(repository):
    Publisher(RepositoryQueue(repository)).publish(shipped("O-1"))
    assert (
        EventRelay(repository, ShopEvent, Broker(), clock=Clock()).run_once().delivered == 1
    )


def test_a_relay_reads_at_least_one(repository):
    with pytest.raises(ContractViolation, match="at least one"):
        EventRelay(repository, ShopEvent, Broker(), batch=0)


def test_an_event_sourced_entity_on_the_double_is_rebuilt_and_guarded(repository):
    wallet = Wallet(id="W-1")
    wallet.credit(10)
    repository.save(wallet)
    first, second = repository.get(Wallet, "W-1"), repository.get(Wallet, "W-1")
    assert first.balance == 10

    first.credit(1)
    repository.save(first)
    second.credit(2)
    with pytest.raises(StaleAggregate):
        repository.save(second)
    assert repository.get(Wallet, "W-1").balance == 11
