"""The two queues the framework ships against one surface: the sync one answers in the call,
the background one crosses to a worker that built its own subscriber."""

import pytest

from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.events import BackgroundQueue, Publisher, Queue, Subscriber, SyncQueue

from .models import FragileSubscriber, ResponseNotify, TicketClosed


def test_both_queues_honour_the_surface(background_queue):
    assert isinstance(SyncQueue(Subscriber()), Queue)
    assert isinstance(background_queue, Queue)


def test_the_sync_queue_answers_in_the_call(sync_queue, heard):
    answers = sync_queue.put(TicketClosed(reason="now"))

    assert len(answers) == 2 and heard["support"] == ["now"]


def test_a_background_queue_delivers_to_both_buses_in_another_process(
    background_queue, answers
):
    """The smallest «do it in the background»: publish here, a Feature and an ApplicationService
    run there, each registered for the same event."""
    Publisher(background_queue).publish(TicketClosed(reason="from the parent"))

    delivered = {answers.get(timeout=30), answers.get(timeout=30)}

    assert delivered == {("feature", "from the parent"), ("app_service", "from the parent")}


def test_a_background_queue_stops_its_worker(background_queue):
    background_queue.stop()

    assert background_queue.process is None
    background_queue.stop()  # idempotent


def test_a_background_queue_cannot_answer_in_the_same_call(background_queue):
    with pytest.raises(ContractViolation, match="cannot answer in the same call"):
        Publisher(background_queue).publish(TicketClosed(reason="x"), ResponseNotify)


def test_a_background_queue_never_forks():
    with pytest.raises(ContractViolation, match="does not fork"):
        BackgroundQueue(Subscriber, context="fork")


def test_a_background_worker_survives_a_subscriber_that_raises(answers):
    """One failing bus must not end the worker: the events after it are still delivered."""
    queue = BackgroundQueue(FragileSubscriber(answers)).start()
    try:
        Publisher(queue).publish(TicketClosed(reason="first"))
        Publisher(queue).publish(TicketClosed(reason="second"))
        delivered = {answers.get(timeout=30), answers.get(timeout=30)}
    finally:
        queue.stop()

    assert delivered == {"first", "second"}


def test_the_trace_rides_beside_the_event_across_the_process_boundary():
    """A worker that started a fresh trace would leave the chain in two unrelated pieces in
    whatever collects them. The producer's `traceparent` travels in the envelope, and the
    consumer adopts it before handing the event to its buses.

    The span is built by hand rather than started from a tracer: a `TracerProvider` can only be
    configured once per process and belongs to another suite, and what is under test here is
    the carrier, not the SDK.
    """
    from opentelemetry import context as otel_context
    from opentelemetry import trace
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    from sincpro_framework.events.queue import _adopted, _carrier

    published_under = 0x4BF92F3577B34DA6A3CE929D0E0E4736
    token = otel_context.attach(
        trace.set_span_in_context(
            NonRecordingSpan(
                SpanContext(
                    trace_id=published_under,
                    span_id=0x00F067AA0BA902B7,
                    is_remote=False,
                    trace_flags=TraceFlags(TraceFlags.SAMPLED),
                )
            )
        )
    )
    try:
        carrier = _carrier()  # what `put` sends beside the payload
    finally:
        otel_context.detach(token)

    assert carrier["traceparent"].startswith("00-4bf92f3577b34da6a3ce929d0e0e4736-")

    # The worker: nothing is current until the carrier is adopted, and then the same trace is.
    assert trace.get_current_span().get_span_context().trace_id != published_under
    with _adopted(carrier):
        assert trace.get_current_span().get_span_context().trace_id == published_under
    assert trace.get_current_span().get_span_context().trace_id != published_under


def test_without_a_trace_or_a_carrier_nothing_breaks():
    from sincpro_framework.events.queue import _adopted, _carrier

    with _adopted(_carrier()):  # no span running: an empty carrier, a plain block
        pass
    with _adopted({}):
        pass


def test_put_is_what_actually_sends_the_trace(background_queue):
    """The envelope `put` builds, read straight off the inbox: three parts, and the third is
    the trace. Asserting on the helpers alone would not catch a `put` that stopped calling
    them."""
    from opentelemetry import context as otel_context
    from opentelemetry import trace
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    token = otel_context.attach(
        trace.set_span_in_context(
            NonRecordingSpan(
                SpanContext(
                    trace_id=0x4BF92F3577B34DA6A3CE929D0E0E4736,
                    span_id=0x00F067AA0BA902B7,
                    is_remote=False,
                    trace_flags=TraceFlags(TraceFlags.SAMPLED),
                )
            )
        )
    )
    try:
        background_queue.put(TicketClosed(reason="traced"))
    finally:
        otel_context.detach(token)

    name, payload, carrier = background_queue.inbox.get(timeout=30)

    assert name == "TicketClosed" and payload["reason"] == "traced"
    assert carrier["traceparent"].startswith("00-4bf92f3577b34da6a3ce929d0e0e4736-")
