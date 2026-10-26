"""What a `DomainEvent` opts into, and what an aggregate opts into to record its changes as one."""

from sincpro_framework.ddd.events.mixins.change_tracking import (
    ChangeTrackingMixin,
    EntityUpdated,
)
from sincpro_framework.ddd.events.mixins.deliverable import DeliverableEventMixin

__all__ = ["ChangeTrackingMixin", "DeliverableEventMixin", "EntityUpdated"]
