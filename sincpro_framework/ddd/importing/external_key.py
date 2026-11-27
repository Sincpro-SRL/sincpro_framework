"""The name another system gives a record: Odoo's `res_partner_42`, a row of the old spreadsheet.

    ExternalKey(origin="odoo", entity_type="Partner", entity_id="0192…", key="res_partner_42")

Context: the identity of a record is a UUID v7 the framework mints, and never derived from a name
someone else owns — the day they correct it, every foreign key would have to change. The name is
kept beside the record instead, as Odoo keeps it in `ir.model.data`, so the next import of the same
file finds the record by it. It is an entity: the same repository stores it, in the same unit of
work as the record it names, and one table holds those of a bounded context
(`external_key_table`).
"""

from dataclasses import dataclass

from sincpro_framework.ddd.entity import Entity


@dataclass
class ExternalKey(Entity):
    """One record's name in one origin: unique by `(scope, origin, entity_type, key)`. Two origins
    naming the same record are two keys of it."""

    origin: str
    """Who gave the name: `"odoo"`, `"legacy_excel"` — the import's own, `"import"` by default."""
    entity_type: str
    """Whose record it names, by class name: `"Partner"`."""
    entity_id: str
    """The identity of the record it names."""
    key: str
    """The name the origin gives the record."""
    scope: str = ""
    """The partition the name lives in — a tenant, a company — so two of them importing the same
    file never collide; `""` is one namespace for everyone. Tenancy fills it with the active
    tenant."""
