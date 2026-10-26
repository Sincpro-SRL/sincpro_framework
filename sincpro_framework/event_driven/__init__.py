"""Event-driven: domain events saved with the change that made them true, delivered by a relay,
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

What travels with a message on a broker is its `Envelope`: `Envelope.headers_for(message,
source=...)` where it is published, `Envelope.of(headers)` where it is consumed.

    domain/          the ports and the policies: Queue, Subscribes, Broker, Envelope, the
                     delivery failure policies, RelayPass
    adapters/        the queues; `adapters.faststream` and `adapters.aws` behind their extras
    services/        the work: EventPublication, EventDispatch, EventRelaying
    entrypoint/      what a project holds: Publisher, Subscriber, EventRelay
"""

from sincpro_framework.event_driven.adapters.background_queue import BackgroundQueue
from sincpro_framework.event_driven.adapters.repository_queue import RepositoryQueue
from sincpro_framework.event_driven.adapters.sync_queue import SyncQueue
from sincpro_framework.event_driven.domain.broker import Broker, Delivery
from sincpro_framework.event_driven.domain.envelope import (
    ATTEMPTS_HEADER,
    EVENT_HEADER,
    HEADERS_ATTRIBUTE,
    Envelope,
)
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
from sincpro_framework.event_driven.domain.relay import RelayPass
from sincpro_framework.event_driven.entrypoint.publisher import AsyncPublisher, Publisher
from sincpro_framework.event_driven.entrypoint.relay import EventRelay
from sincpro_framework.event_driven.entrypoint.subscriber import AsyncSubscriber, Subscriber

__all__ = [
    "ATTEMPTS_HEADER",
    "EVENT_HEADER",
    "HEADERS_ATTRIBUTE",
    "AsyncPublisher",
    "AsyncSubscriber",
    "BackgroundQueue",
    "Broker",
    "Delivery",
    "DeliveryFailurePolicy",
    "Envelope",
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
