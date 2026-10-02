"""Event-driven: domain events kept with the change that made them true, delivered by a relay,
carried by queues to whoever listens.

    repository.save(invoice)               the invoice, and its events in the context's event table
          │
    EventRelay                             the deliverable events not delivered yet, oldest first
          │
    Publisher(Queue)                       SyncQueue · BackgroundQueue · FastStreamQueue
          │
    Subscriber(buses) · the broker's consumers

    queue = SyncQueue(Subscriber(planning, assurance))     # or BackgroundQueue(build).start()
    Publisher(queue).publish(event)                        # or publish(event, ResponseDTO)

Nothing is wired by default. A project builds the queue it wants with the buses it names, and
publishes when a Feature decides to — directly, or through its repository (`RepositoryQueue`),
to be delivered by the relay once the transaction committed.

    domain/          the ports and policies: Queue, the delivery failure policies
    services/        what runs on them: the subscriber
    adapters/        the queues; `adapters.faststream` behind its extra
    infrastructure/  the trace that travels beside an event
    entrypoint/      what a project holds: Publisher, EventRelay
"""

from sincpro_framework.event_driven.adapters.background_queue import BackgroundQueue
from sincpro_framework.event_driven.adapters.repository_queue import RepositoryQueue
from sincpro_framework.event_driven.adapters.sync_queue import SyncQueue
from sincpro_framework.event_driven.domain.failure import (
    DEFAULT_FAILURE_POLICY,
    BackoffStrategy,
    DeliveryFailurePolicy,
    ExponentialBackoff,
    FailureDecision,
    FailureOutcome,
    FixedBackoff,
    HandlingFailure,
    ParkAndContinue,
    RetryInPlace,
    RetryLater,
    SkipAndContinue,
)
from sincpro_framework.event_driven.domain.queue import Queue
from sincpro_framework.event_driven.entrypoint.publisher import AsyncPublisher, Publisher
from sincpro_framework.event_driven.entrypoint.relay import EventRelay, RelayPass
from sincpro_framework.event_driven.services.subscriber import AsyncSubscriber, Subscriber

__all__ = [
    "AsyncPublisher",
    "AsyncSubscriber",
    "BackgroundQueue",
    "BackoffStrategy",
    "DEFAULT_FAILURE_POLICY",
    "DeliveryFailurePolicy",
    "EventRelay",
    "ExponentialBackoff",
    "FailureDecision",
    "FailureOutcome",
    "FixedBackoff",
    "HandlingFailure",
    "ParkAndContinue",
    "Publisher",
    "Queue",
    "RelayPass",
    "RepositoryQueue",
    "RetryInPlace",
    "RetryLater",
    "SkipAndContinue",
    "SyncQueue",
    "Subscriber",
]
