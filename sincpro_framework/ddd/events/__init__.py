"""Events in the domain: the fact an aggregate records, and what an event opts into.

    domain_event.py     DomainEvent — the envelope every fact carries, and its wire name
    mixins.py           DeliverableEventMixin — a contract other bounded contexts may hear

The vocabulary only. An event is an entity: the repository keeps it like any other, in the
bounded context's event table; what delivers and carries it (`EventRelay`, `Publisher`, queues)
is `sincpro_framework.event_driven`.
"""

from sincpro_framework.ddd.events.domain_event import NAME, DomainEvent
from sincpro_framework.ddd.events.mixins.deliverable import DeliverableEventMixin

__all__ = ["NAME", "DomainEvent", "DeliverableEventMixin"]
