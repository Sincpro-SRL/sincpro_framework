"""Publishing on commit: what an aggregate recorded goes to a publisher once the write committed —
never before, never after a rollback, once — and the stored event says whether it went out."""

from concurrent.futures import Future
from dataclasses import dataclass

import pytest

from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import DeliverableEventMixin, DomainEvent, Entity
from sincpro_framework.ddd.entity import utc_now
from sincpro_framework.ddd.repositories import PublishOnCommit


@dataclass(kw_only=True)
class ShopEvent(DomainEvent):
    name = "tests.commit.v1.event"


@dataclass(kw_only=True)
class OrderShipped(DeliverableEventMixin, ShopEvent):
    name = "tests.commit.order.v1.shipped"


@dataclass(kw_only=True)
class StockCounted(ShopEvent):
    name = "tests.commit.v1.stock_counted"


@dataclass
class Order(Entity):
    def ship(self) -> None:
        self.record(OrderShipped())
        self.record(StockCounted())


@dataclass
class Shelf(Entity):
    def ship(self) -> None:
        self.record(OrderShipped())


class Broker:
    def __init__(self) -> None:
        self.sent: list[DomainEvent] = []
        self.down = False

    def publish(self, event: DomainEvent) -> None:
        if self.down:
            raise ConnectionError("the broker is down")
        self.sent.append(event)


def shipped(repository: MemoryRepository, order: Order) -> OrderShipped:
    """The stored row of the order's shipment, as the event table holds it."""
    [stored] = repository.fetch_all(OrderShipped).items
    assert stored.entity_id == order.id
    return stored


def test_inside_a_unit_of_work_it_goes_out_after_the_commit_and_never_after_a_rollback():
    broker = Broker()
    repository = MemoryRepository(publish_on_commit=broker)

    with repository.context() as unit:
        order = Order()
        order.ship()
        unit.save(order)
        assert broker.sent == []
    assert [type(one) for one in broker.sent] == [OrderShipped]

    with pytest.raises(RuntimeError), repository.context() as unit:
        undone = Order()
        undone.ship()
        unit.save(undone)
        raise RuntimeError("undone")
    assert len(broker.sent) == 1


def test_only_deliverable_events_go_out_and_the_rest_stay_with_the_aggregate():
    broker = Broker()
    repository = MemoryRepository(publish_on_commit=broker)
    order = Order()
    order.ship()

    repository.save(order)

    assert [type(one) for one in broker.sent] == [OrderShipped]
    assert [type(one) for one in order.pull_events()] == [StockCounted]


def test_saving_the_aggregate_again_never_publishes_twice():
    broker = Broker()
    repository = MemoryRepository(publish_on_commit=broker)
    order = Order()
    order.ship()

    repository.save(order)
    repository.save(order)

    assert len(broker.sent) == 1


def test_the_stored_event_says_when_it_went_out_and_how_many_attempts_it_took():
    repository = MemoryRepository(publish_on_commit=Broker())
    order = Order()
    order.ship()

    repository.save(order)

    stored = shipped(repository, order)
    assert stored.delivered_at is not None
    assert stored.delivery_attempts == 1


def test_the_stored_event_says_why_it_did_not_go_out():
    broker = Broker()
    broker.down = True
    repository = MemoryRepository(publish_on_commit=broker)
    order = Order()
    order.ship()

    repository.save(order)

    stored = shipped(repository, order)
    assert stored.delivered_at is None
    assert "the broker is down" in stored.delivery["last_error"]


def test_a_forwarded_event_is_stamped_once_the_forwarding_delivered_it():
    class Forwarded:
        attempts = 3

        def __init__(self) -> None:
            self.delivered_at = utc_now()
            self.last_error = None

    class LaterBroker:
        def __init__(self) -> None:
            self.pending: list[Future] = []

        def publish(self, event: DomainEvent) -> Future:
            later: Future = Future()
            self.pending.append(later)
            return later

    broker = LaterBroker()
    repository = MemoryRepository(publish_on_commit=broker)
    order = Order()
    order.ship()
    repository.save(order)
    assert shipped(repository, order).delivered_at is None

    broker.pending[0].set_result(Forwarded())

    stored = shipped(repository, order)
    assert stored.delivered_at is not None
    assert stored.delivery_attempts == 3


def test_entities_and_when_narrow_what_goes_out():
    broker = Broker()
    repository = MemoryRepository(
        publish_on_commit=PublishOnCommit(
            broker, entities=[Shelf], when=lambda event: not event.label
        )
    )
    order, shelf, labelled = Order(), Shelf(), Shelf()
    order.ship()
    shelf.ship()
    labelled.record(OrderShipped(label={"es": "oculto"}))

    repository.save([order, shelf, labelled])

    assert [one.entity_id for one in broker.sent] == [shelf.id]


def test_a_subclass_picks_a_publisher_per_event_and_what_goes_out():
    rabbit, audit = Broker(), Broker()

    class ShopEvents(PublishOnCommit):
        def publishes(self, event: DomainEvent) -> bool:
            return isinstance(event, ShopEvent)

        def publisher_of(self, event: DomainEvent) -> Broker:
            return rabbit if isinstance(event, OrderShipped) else audit

    repository = MemoryRepository(publish_on_commit=ShopEvents(rabbit))
    order = Order()
    order.ship()

    repository.save(order)

    assert [type(one) for one in rabbit.sent] == [OrderShipped]
    assert [type(one) for one in audit.sent] == [StockCounted]
