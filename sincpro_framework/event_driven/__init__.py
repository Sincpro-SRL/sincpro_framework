"""Event-driven: messages published to a queue, carried to whoever listens — sent in the call, or
held and forwarded in order while the broker is down.

    Publisher(Queue)                       SyncQueue · BackgroundQueue · FastStreamQueue · AWS
          │   forwarding=True              held in line, sent in order on a thread of its own
          ▼
    Subscriber(buses) · the broker's consumers

    queue = SyncQueue(Subscriber(planning, assurance))     # or BackgroundQueue(build).start()
    Publisher(queue).publish(event)                        # or publish(event, ResponseDTO)
    Publisher(rabbit, forwarding=True).publish(event)      # Future[Forwarded], at once

Nothing is wired by default. A project builds the queue it wants with the buses it names, and
publishes when a Feature decides to — or has its repository publish what a write recorded once it
committed (`publish_on_commit=`, in `ddd.repositories`). Any message is published; a `DomainEvent`
is the one this framework recommends, and a queue that writes bytes takes a `serializer` for the
project's own.

What travels with a message on a broker is its `Envelope`: `Envelope.headers_for(message,
source=...)` where it is published, `Envelope.of(headers)` where it is consumed.

    domain/          the ports and what they report: Queue, Subscribes, Broker, Envelope,
                     Forwarded, ForwardingHook
    adapters/        the queues; `adapters.faststream` and `adapters.aws` behind their extras
    services/        the work: EventPublication, Forwarding, EventDispatch
    entrypoint/      what a project holds: Publisher, Subscriber
"""

from sincpro_framework.event_driven.adapters.background_queue import BackgroundQueue
from sincpro_framework.event_driven.adapters.sync_queue import SyncQueue
from sincpro_framework.event_driven.domain.broker import Broker, Delivery
from sincpro_framework.event_driven.domain.envelope import (
    ATTEMPTS_HEADER,
    EVENT_HEADER,
    HEADERS_ATTRIBUTE,
    Envelope,
)
from sincpro_framework.event_driven.domain.forwarding import Forwarded, ForwardingHook
from sincpro_framework.event_driven.domain.queue import Queue
from sincpro_framework.event_driven.entrypoint.publisher import AsyncPublisher, Publisher
from sincpro_framework.event_driven.entrypoint.subscriber import AsyncSubscriber, Subscriber
from sincpro_framework.event_driven.services.forwarding import Forwarding

__all__ = [
    "ATTEMPTS_HEADER",
    "EVENT_HEADER",
    "HEADERS_ATTRIBUTE",
    "AsyncPublisher",
    "AsyncSubscriber",
    "BackgroundQueue",
    "Broker",
    "Delivery",
    "Envelope",
    "Forwarded",
    "Forwarding",
    "ForwardingHook",
    "Publisher",
    "Queue",
    "Subscriber",
    "SyncQueue",
]
