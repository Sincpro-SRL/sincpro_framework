"""The tables a project declares, one file per kind — what the framework's conventions need.

    entity.py          entity_table, entity_columns         every Entity
    audit.py           audit_columns                        AuditedMixin
    archive.py         archive_columns                      ArchivableMixin
    events.py          event_table, event_columns           the context's domain events
    external_keys.py   external_key_table                   the names other systems give records
    numbering.py       numbering_table                      gapless numbers
    idempotency.py     idempotency_table                    a write run exactly once
    auth.py            key_value_table, api_key_table,      what auth keeps on a database
                       grant_assignment_table, entitlement_table, refresh_token_table
    runtime_use_cases.py  use_case_table                    the use cases a context stores
    tenancy.py         tenant_columns, unique_per_tenant,   tenancy's recipes
                       tenant_foreign_key, tenant_table, membership_table
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
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.auth import (
    api_key_table,
    entitlement_table,
    grant_assignment_table,
    key_value_table,
    refresh_token_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.entity import (
    entity_columns,
    entity_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.events import (
    event_columns,
    event_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.external_keys import (
    external_key_table,
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
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.tenancy import (
    membership_table,
    tenant_columns,
    tenant_foreign_key,
    tenant_table,
    unique_per_tenant,
)

__all__ = [
    "SlotType",
    "api_key_table",
    "archive_columns",
    "audit_columns",
    "entitlement_table",
    "entity_columns",
    "entity_table",
    "event_columns",
    "event_table",
    "external_key_table",
    "grant_assignment_table",
    "idempotency_table",
    "key_value_table",
    "membership_table",
    "numbering_table",
    "properties_column",
    "refresh_token_table",
    "slot_field",
    "tenant_columns",
    "tenant_foreign_key",
    "tenant_table",
    "unique_per_tenant",
    "use_case_table",
]
