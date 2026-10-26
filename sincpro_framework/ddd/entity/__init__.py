"""`sincpro_framework.ddd.entity` — what an aggregate is: `Entity`, `AuditedMixin`,
`ArchivableMixin`, `EventSourcedMixin`, `Translated`, `new_entity_id`, `utc_now`, `uuid7`.

    from sincpro_framework.ddd.entity import Entity, AuditedMixin

One import for the whole story; `entity.py`, `mixins/audited.py` and `mixins/archivable.py` are
how it is organized underneath, not something a caller needs to know. What an aggregate records
— `DomainEvent`, and `ChangeTrackingMixin` with its `EntityUpdated` — is
`sincpro_framework.ddd.events`, which builds on this package and never the other way round.
"""

from sincpro_framework.common.ids import new_entity_id, uuid7
from sincpro_framework.ddd.entity.entity import (
    RECORDED,
    Entity,
    json_serializer,
    utc_now,
)
from sincpro_framework.ddd.entity.entity_meta import Translated
from sincpro_framework.ddd.entity.mixins.archivable import ArchivableMixin
from sincpro_framework.ddd.entity.mixins.audited import AuditedMixin
from sincpro_framework.ddd.entity.mixins.event_sourced import EventSourcedMixin

__all__ = [
    "RECORDED",
    "ArchivableMixin",
    "AuditedMixin",
    "EventSourcedMixin",
    "Entity",
    "Translated",
    "json_serializer",
    "new_entity_id",
    "utc_now",
    "uuid7",
]
