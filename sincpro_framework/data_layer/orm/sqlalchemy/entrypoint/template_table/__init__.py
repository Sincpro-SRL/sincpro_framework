"""The tables a project declares, one file per kind — what the framework's conventions need.

    entity.py          entity_table, entity_columns         every Entity
    audit.py           audit_columns                        AuditedMixin
    archive.py         archive_columns                      ArchivableMixin
    events.py          event_table, event_columns           the context's domain events
    numbering.py       numbering_table                      gapless numbers
    idempotency.py     idempotency_table                    a write run exactly once
    runtime_use_cases.py  use_case_table                    the use cases a context stores
    properties.py      properties_column, slot_field        the slot an extensible entity reserves

Declared by the project, in its own metadata and migrations, like any other table; mapped with
`map_aggregates` and `map_events`.
"""

from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.archive import (
    archive_columns,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.audit import (
    audit_columns,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.entity import (
    entity_columns,
    entity_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.events import (
    event_columns,
    event_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.idempotency import (
    idempotency_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.numbering import (
    numbering_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.properties import (
    SlotType,
    properties_column,
    slot_field,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.runtime_use_cases import (
    use_case_table,
)

__all__ = [
    "SlotType",
    "archive_columns",
    "audit_columns",
    "entity_columns",
    "entity_table",
    "event_columns",
    "event_table",
    "idempotency_table",
    "numbering_table",
    "properties_column",
    "slot_field",
    "use_case_table",
]
