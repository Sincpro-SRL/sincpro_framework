"""Events: a publisher that emits, a queue that carries, a subscriber made of buses.

    from sincpro_framework.events import SyncQueue, BackgroundQueue, Publisher, Subscriber

    queue = SyncQueue(Subscriber(planning, assurance))   # or BackgroundQueue(build_subscriber).start()
    Publisher(queue).publish(event)                       # or publish(event, ResponseDTO)

Nothing is wired by default and nothing is stored. A project builds the queue it wants with
the buses it names, and publishes when a Feature decides to.
"""

from sincpro_framework.events.publisher import AsyncPublisher, Publisher
from sincpro_framework.events.queue import BackgroundQueue, Queue, SyncQueue
from sincpro_framework.events.subscriber import AsyncSubscriber, Subscriber

__all__ = [
    "AsyncPublisher",
    "AsyncSubscriber",
    "SyncQueue",
    "BackgroundQueue",
    "Publisher",
    "Queue",
    "Subscriber",
]
