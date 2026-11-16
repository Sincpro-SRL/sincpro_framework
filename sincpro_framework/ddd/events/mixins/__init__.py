"""What a `DomainEvent` opts into, and what an aggregate opts into to record its changes as one."""

from sincpro_framework.ddd.events.mixins.change_tracking import (
    ChangeTrackingMixin,
    EntityArchived,
    EntityDeleted,
    EntityRestored,
    EntityUpdated,
)
from sincpro_framework.ddd.events.mixins.deliverable import DeliverableEventMixin

__all__ = [
    "ChangeTrackingMixin",
    "DeliverableEventMixin",
    "EntityArchived",
    "EntityDeleted",
    "EntityRestored",
    "EntityUpdated",
]
