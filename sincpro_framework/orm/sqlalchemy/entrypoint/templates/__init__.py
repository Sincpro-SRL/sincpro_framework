"""The tables a project declares, one file per kind — what the framework's conventions need.

    entity.py          entity_table, entity_columns         every Entity
    audit.py           audit_columns                        AuditedMixin
    archive.py         archive_columns                      ArchivableMixin
    events.py          event_table, event_columns           the context's domain events
    numbering.py       numbering_table                      gapless numbers

Declared by the project, in its own metadata and migrations, like any other table; mapped with
`map_aggregates` and `map_events`.
"""

from sincpro_framework.orm.sqlalchemy.entrypoint.templates.archive import archive_columns
from sincpro_framework.orm.sqlalchemy.entrypoint.templates.audit import audit_columns
from sincpro_framework.orm.sqlalchemy.entrypoint.templates.entity import (
    entity_columns,
    entity_table,
)
from sincpro_framework.orm.sqlalchemy.entrypoint.templates.events import (
    event_columns,
    event_table,
)
from sincpro_framework.orm.sqlalchemy.entrypoint.templates.numbering import numbering_table

__all__ = [
    "archive_columns",
    "audit_columns",
    "entity_columns",
    "entity_table",
    "event_columns",
    "event_table",
    "numbering_table",
]
