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

    domain/          the port and the policies: Queue, the delivery failure policies
    adapters/        the queues; `adapters.faststream` behind its extra
    entrypoint/      what a project holds: Publisher, Subscriber, EventRelay
"""

from sincpro_framework.event_driven.adapters.background_queue import BackgroundQueue
from sincpro_framework.event_driven.adapters.repository_queue import RepositoryQueue
from sincpro_framework.event_driven.adapters.sync_queue import SyncQueue
from sincpro_framework.event_driven.domain.failure import (
    DeliveryFailurePolicy,
    ExponentialBackoff,
    FailureDecision,
    FixedBackoff,
    ParkAndContinue,
    RetryInPlace,
    RetryLater,
    SkipAndContinue,
)
from sincpro_framework.event_driven.domain.queue import Queue
from sincpro_framework.event_driven.entrypoint.publisher import AsyncPublisher, Publisher
from sincpro_framework.event_driven.entrypoint.relay import EventRelay, RelayPass
from sincpro_framework.event_driven.entrypoint.subscriber import AsyncSubscriber, Subscriber

__all__ = [
    "AsyncPublisher",
    "AsyncSubscriber",
    "BackgroundQueue",
    "DeliveryFailurePolicy",
    "EventRelay",
    "ExponentialBackoff",
    "FailureDecision",
    "FixedBackoff",
    "ParkAndContinue",
    "Publisher",
    "Queue",
    "RelayPass",
    "RepositoryQueue",
    "RetryInPlace",
    "RetryLater",
    "SkipAndContinue",
    "Subscriber",
    "SyncQueue",
]
