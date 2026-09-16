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
