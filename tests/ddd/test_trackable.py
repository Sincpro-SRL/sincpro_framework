"""`TrackableMixin` is an opt-in mixin, the same shape as `AuditedMixin`/`ArchivableMixin` on `Entity`: a
plain `DomainEvent` stays exactly as simple as it always was; only a class that combines
`TrackableMixin` carries a delivery status.
"""

from dataclasses import dataclass

from sincpro_framework.ddd.events import DomainEvent, EventStatus, TrackableMixin


@dataclass(kw_only=True)
class OrderShipped(TrackableMixin, DomainEvent):
    order_id: str


@dataclass(kw_only=True)
class PlainThingHappened(DomainEvent):
    x: int


def test_a_plain_domain_event_carries_no_status():
    assert not hasattr(PlainThingHappened(x=1), "status")


def test_a_trackable_event_starts_pending():
    event = OrderShipped(order_id="ORD-1")

    assert event.status is EventStatus.PENDING
    assert event.attempts == 0
    assert event.error_message is None
    assert event.acknowledged_at is None
    assert event.failed_at is None


def test_mark_processing_advances_status_and_counts_the_attempt():
    event = OrderShipped(order_id="ORD-1")

    event.mark_processing()

    assert event.status is EventStatus.PROCESSING
    assert event.attempts == 1

    event.mark_processing()
    assert event.attempts == 2


def test_mark_acknowledged_stamps_when_it_happened():
    event = OrderShipped(order_id="ORD-1")

    event.mark_processing()
    event.mark_acknowledged()

    assert event.status is EventStatus.ACKNOWLEDGED
    assert event.acknowledged_at is not None


def test_mark_failed_carries_the_error():
    event = OrderShipped(order_id="ORD-1")

    event.mark_processing()
    event.mark_failed("boom")

    assert event.status is EventStatus.FAILED
    assert event.error_message == "boom"
    assert event.failed_at is not None


def test_mark_cancelled_just_sets_the_status():
    event = OrderShipped(order_id="ORD-1")

    event.mark_cancelled()

    assert event.status is EventStatus.CANCELLED


def test_a_trackable_event_still_round_trips_through_json():
    event = OrderShipped(order_id="ORD-1")
    event.mark_processing()
    event.mark_failed("boom")

    rebuilt = OrderShipped.from_json(event.as_json())

    assert rebuilt == event
    assert rebuilt.status is EventStatus.FAILED
