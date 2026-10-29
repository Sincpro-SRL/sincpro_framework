"""Events in the domain: the fact an aggregate records, and what an event opts into.

    domain_event.py               DomainEvent — the envelope every fact carries, and its wire name
    mixins/deliverable.py         DeliverableEventMixin — a contract other bounded contexts may hear,
                                  and when it went out
    mixins/change_tracking.py     ChangeTrackingMixin — an aggregate records what changed as one
                                  `EntityUpdated`

The vocabulary only. An event is an entity: the repository stores it like any other, in the
bounded context's event table; what publishes it once a write committed is the repository's
`publish_on_commit=`, and what carries it (`Publisher`, `Forwarding`, queues) is
`sincpro_framework.event_driven`.
"""

from sincpro_framework.ddd.events.domain_event import NAME, DomainEvent
from sincpro_framework.ddd.events.mixins.change_tracking import (
    ChangeTrackingMixin,
    EntityUpdated,
)
from sincpro_framework.ddd.events.mixins.deliverable import DeliverableEventMixin

__all__ = [
    "NAME",
    "ChangeTrackingMixin",
    "DeliverableEventMixin",
    "DomainEvent",
    "EntityUpdated",
]
