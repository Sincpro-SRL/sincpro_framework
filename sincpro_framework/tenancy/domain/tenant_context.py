"""The tenant context — the tenants an execution works in, read from the execution context.

Three sets, the shape Odoo gives a company (`env.company`, `allowed_company_ids`,
`user.company_ids`):

    active      tenant_id     where a write goes; one of the selected
    selected    tenant_ids    what a read sees; within the allowed
    allowed     memberships   what the caller may select; never sent by the caller

The execution context holds `active` and `selected` under the framework's own keys
(`ContextKey.TENANT_ID`, `ContextKey.TENANT_IDS`); `allowed` is asked of `Memberships`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sincpro_framework.context.domain.keys import ContextKey


@dataclass(frozen=True)
class TenantContext:
    """The tenants one execution works in.

    TenantContext.of({"tenant_id": "acme"})
        ->  TenantContext(active="acme", selected=("acme",))
    TenantContext.of({"tenant_ids": ["acme", "acme-sur"]})
        ->  TenantContext(active=None, selected=("acme", "acme-sur"))
    TenantContext.of({})
        ->  TenantContext(active=None, selected=())       no tenant named
    """

    active: str | None = None
    selected: tuple[str, ...] = ()

    @staticmethod
    def of(values: Mapping[Any, Any]) -> "TenantContext":
        """The tenant context an execution context holds: `tenant_ids` as the selection, the
        active tenant in it when the selection does not name it; the active alone when no
        selection was given."""
        active = values.get(ContextKey.TENANT_ID) or None
        named = values.get(ContextKey.TENANT_IDS) or ()
        if isinstance(named, str):
            named = (named,)
        selected = tuple(dict.fromkeys(str(one) for one in named if one))
        if active is not None and active not in selected:
            selected = (str(active), *selected)
        return TenantContext(None if active is None else str(active), selected)

    @property
    def is_empty(self) -> bool:
        """Whether no tenant is named at all."""
        return not self.selected
