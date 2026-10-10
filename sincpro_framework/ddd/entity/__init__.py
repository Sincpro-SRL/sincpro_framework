"""`sincpro_framework.ddd.entity` — a package now, one file per concern, same public names as
when it was a single module: `Entity`, `AuditedMixin`, `ArchivableMixin`, `ChangeTrackingMixin`,
`EntityUpdated`, `Translated`, `new_entity_id`, `utc_now`, `uuid7`.

    from sincpro_framework.ddd.entity import Entity, AuditedMixin, ChangeTrackingMixin

Still the whole story in one import; `entity.py`, `mixins/audited.py`, `mixins/archivable.py`
and `mixins/change_tracking.py` are how it is organized underneath, not something a caller needs to
know.
"""

from sincpro_framework.common.ids import new_entity_id, uuid7
from sincpro_framework.ddd.entity.entity import (
    RECORDED,
    Entity,
    Translated,
    json_serializer,
    utc_now,
)
from sincpro_framework.ddd.entity.mixins.archivable import ArchivableMixin
from sincpro_framework.ddd.entity.mixins.audited import AuditedMixin
from sincpro_framework.ddd.entity.mixins.change_tracking import (
    ChangeTrackingMixin,
    EntityUpdated,
)
from sincpro_framework.ddd.entity.mixins.event_sourced import EventSourcedMixin

__all__ = [
    "RECORDED",
    "ArchivableMixin",
    "AuditedMixin",
    "ChangeTrackingMixin",
    "EventSourcedMixin",
    "Entity",
    "EntityUpdated",
    "Translated",
    "json_serializer",
    "new_entity_id",
    "utc_now",
    "uuid7",
]
