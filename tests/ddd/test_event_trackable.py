"""`EventTrackableMixin` is an opt-in mixin, the same shape as `AuditedMixin`/`ArchivableMixin` on `Entity`: a
plain `DomainEvent` stays exactly as simple as it always was; only a class that combines
`EventTrackableMixin` carries a delivery status.
"""

from dataclasses import dataclass

from sincpro_framework.ddd.events import DomainEvent, EventStatus, EventTrackableMixin


@dataclass(kw_only=True)
class OrderShipped(EventTrackableMixin, DomainEvent):
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


def test_an_event_chains_to_the_one_that_caused_it():
    incoming = PlainThingHappened(x=1)
    caused = PlainThingHappened(x=2).caused_by(incoming)

    assert caused.causation_id == incoming.id
    assert caused.correlation_id == incoming.id  # nobody correlated the head: it is the head


def test_the_correlation_survives_the_whole_chain_and_the_causation_moves():
    first = PlainThingHappened(x=1)
    second = PlainThingHappened(x=2).caused_by(first)
    third = PlainThingHappened(x=3).caused_by(second)

    assert [one.correlation_id for one in (second, third)] == [first.id, first.id]
    assert third.causation_id == second.id  # what directly led here, not the head


def test_an_explicit_correlation_is_the_one_that_travels():
    request = PlainThingHappened(x=0, correlation_id="req-7")
    caused = PlainThingHappened(x=1).caused_by(request)

    assert caused.correlation_id == "req-7" and caused.causation_id == request.id


def test_the_cause_is_left_untouched():
    incoming = PlainThingHappened(x=1)
    PlainThingHappened(x=2).caused_by(incoming)

    assert incoming.causation_id is None and incoming.correlation_id is None
