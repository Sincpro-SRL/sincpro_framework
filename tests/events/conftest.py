"""Fixtures for the event tests: fresh bounded contexts per test, the subscriber that holds
them, the queues, and a publisher on each.

Every fixture builds new `UseFramework` instances, so no test hears what another published.
The background queue is a yield fixture: whatever a test does, the worker process is stopped.
"""

import multiprocessing
from collections.abc import Iterator

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.events import BackgroundQueue, Publisher, Subscriber, SyncQueue

from .models import ReportingSubscriber, auditing_bus, failing_bus, notifying_bus


@pytest.fixture
def heard() -> dict[str, list[str]]:
    """What each bounded context received, by context name."""
    return {"support": [], "audit": []}


@pytest.fixture
def feature_bus(heard: dict[str, list[str]]) -> UseFramework:
    """A context with a Feature on `[CommandNotify, TicketClosed]`."""
    return notifying_bus(heard["support"], "support")


@pytest.fixture
def app_service_bus(heard: dict[str, list[str]]) -> UseFramework:
    """A context with an ApplicationService on `[CommandAudit, TicketClosed]`."""
    return auditing_bus(heard["audit"], "audit")


@pytest.fixture
def silent_bus() -> UseFramework:
    """A context that registered nothing for the event."""
    return UseFramework("silent", log_after_execution=False)


@pytest.fixture
def broken_bus() -> UseFramework:
    return failing_bus()


@pytest.fixture
def subscriber(
    feature_bus: UseFramework, app_service_bus: UseFramework, silent_bus: UseFramework
) -> Subscriber:
    """The three contexts, handed in explicitly, in this order."""
    return Subscriber(feature_bus, app_service_bus, silent_bus)


@pytest.fixture
def sync_queue(subscriber: Subscriber) -> SyncQueue:
    return SyncQueue(subscriber)


@pytest.fixture
def publisher(sync_queue: SyncQueue) -> Publisher:
    return Publisher(sync_queue)


@pytest.fixture
def answers() -> "multiprocessing.Queue":
    """Where a worker process reports what it delivered."""
    return multiprocessing.get_context("spawn").Queue()


@pytest.fixture
def background_queue(answers: "multiprocessing.Queue") -> Iterator[BackgroundQueue]:
    """A started worker with two buses of its own; stopped whatever the test did."""
    queue = BackgroundQueue(ReportingSubscriber(answers)).start()
    yield queue
    queue.stop()
