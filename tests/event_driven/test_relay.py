"""The relay runs over any repository: on the in-memory double it delivers, retries, parks and
keeps an entity's order the same way it does on a database."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from sincpro_framework import ProgrammingError
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    DeliverableEventMixin,
    DomainEvent,
    Entity,
    EventSourcedMixin,
)
from sincpro_framework.ddd.exceptions import StaleAggregate
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


# --- the order holds across passes ------------------------------------------------------------


def test_a_retry_later_holds_its_entity_in_the_passes_before_its_retry(repository):
    """The first event of O-1 waits ten seconds for its retry; a pass in between, with the
    broker back, still keeps the second one of O-1 behind it."""
    repository.save([shipped("O-1", "dhl"), shipped("O-1", "ups")])
    broker = Broker()
    broker.failing = {"O-1"}
    clock = Clock()
    policy = RetryLater(attempts=3, backoff=FixedBackoff(timedelta(seconds=10)))
    relay = EventRelay(repository, ShopEvent, broker, policy, clock=clock)

    relay.run_once()
    broker.failing = set()
    clock.now += timedelta(seconds=5)
    between = relay.run_once()
    assert (between.read, between.held, broker.sent) == (1, 1, [])

    clock.now += timedelta(seconds=5)
    relay.run_once()
    assert [getattr(one, "carrier") for one in broker.sent] == ["dhl", "ups"]


def test_a_retry_in_place_holds_every_later_event_until_its_retry(repository):
    repository.save([shipped("O-1"), shipped("O-2")])
    broker = Broker()
    broker.failing = {"O-1"}
    clock = Clock()
    policy = RetryInPlace(attempts=3, backoff=FixedBackoff(timedelta(seconds=10)))
    relay = EventRelay(repository, ShopEvent, broker, policy, clock=clock)

    relay.run_once()
    broker.failing = set()
    clock.now += timedelta(seconds=5)
    between = relay.run_once()
    assert (between.held, broker.sent) == (1, [])  # O-2 waits behind O-1

    clock.now += timedelta(seconds=5)
    assert relay.run_once().delivered == 2
    assert [one.entity_id for one in broker.sent] == ["O-1", "O-2"]


def test_a_parked_event_lets_the_later_events_of_its_entity_go(repository):
    """Parked is a dead letter: what came after it on the same entity is not held behind it."""
    repository.save([shipped("O-1", "dhl"), shipped("O-1", "ups")])
    broker = ByCarrier()
    broker.failing = {"dhl"}
    relay = EventRelay(repository, ShopEvent, broker, RetryLater(attempts=1), clock=Clock())

    first = relay.run_once()
    assert (first.parked, first.held, first.delivered) == (1, 0, 1)
    assert [getattr(one, "carrier") for one in broker.sent] == ["ups"]


# --- what is retried, and when ----------------------------------------------------------------


class ByCarrier(Broker):
    def publish(self, event: DomainEvent) -> None:
        if getattr(event, "carrier") in self.failing:
            raise ConnectionError("the carrier is down")
        self.sent.append(event)


class DoesNotFit(ValueError):
    failure_kind = "invalid"


class RateLimited(ConnectionError):
    failure_kind = "exhausted"
    retry_after = 60


class ClassifyingBroker(Broker):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def publish(self, event: DomainEvent) -> None:
        raise self.error


def test_an_error_that_fails_the_same_way_every_time_is_parked_at_once(repository):
    """The kinds a queue consumer dead-letters — here `invalid` — are never tried again."""
    repository.save([shipped("O-1")])
    relay = EventRelay(
        repository, ShopEvent, ClassifyingBroker(DoesNotFit("no")), RetryInPlace(attempts=5)
    )

    assert relay.run_once().parked == 1


def test_an_error_that_says_how_long_to_wait_is_not_tried_before_that(repository):
    repository.save([shipped("O-1")])
    clock = Clock()
    policy = RetryInPlace(attempts=5, backoff=FixedBackoff(timedelta(seconds=1)))
    relay = EventRelay(
        repository,
        ShopEvent,
        ClassifyingBroker(RateLimited("slow down")),
        policy,
        clock=clock,
    )

    relay.run_once()
    [waiting] = repository.fetch_all(OrderShipped).items
    assert waiting.next_delivery_at == clock.now + timedelta(seconds=60)


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
    with pytest.raises(ProgrammingError, match="at least one"):
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
