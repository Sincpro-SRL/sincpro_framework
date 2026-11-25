"""`TokenSources`: one credential per tenant — the tenant's Odoo, its SIAT token, its bank — and
never another tenant's.

    TokenSources({"bo-001": StaticToken(t1, header="apikey", scheme="TokenApi")})
    TokenSources(lambda tenant: StaticToken(registry.siat_token_of(tenant), ...) or None)

Context: the tenant is the request's, else the identity's in play. A tenant with no credential is
`NoCredentialsFor` (403) — never a default token: that is how one tenant's invoices end up signed
with another's. Sources resolved by a function are kept per tenant, the least recently used let
go past `max_tenants`, keyed by the tenant — never by a token.
"""

import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any

from sincpro_framework.auth.client.domain.outbound import OutgoingRequest, TokenSource
from sincpro_framework.auth.common.domain import exceptions


class TokenSources(TokenSource):
    """A `TokenSource` per tenant, resolved from a table or a function."""

    def __init__(
        self,
        resolve: Mapping[str, TokenSource] | Callable[[str], TokenSource | None],
        name: str = "per-tenant",
        max_tenants: int = 1024,
    ) -> None:
        self._resolve = resolve
        self.name = name
        self.max_tenants = max_tenants
        self._sources: OrderedDict[str, TokenSource] = OrderedDict()
        self._lock = threading.Lock()

    def source_of(self, tenant: str | None) -> TokenSource:
        """The tenant's source — `NoCredentialsFor` when it has none, `ProgrammingError` when no
        tenant is known at all."""
        if tenant is None:
            raise exceptions.ProgrammingError(
                f"{self.name} keeps credentials per tenant: the call names no tenant"
            )
        if isinstance(self._resolve, Mapping):
            found = self._resolve.get(tenant)
        else:
            with self._lock:
                found = self._sources.get(tenant)
                if found is not None:
                    self._sources.move_to_end(tenant)
            if found is None:
                found = self._resolve(tenant)
                if found is not None:
                    with self._lock:
                        self._sources[tenant] = found
                        while len(self._sources) > self.max_tenants:
                            self._sources.popitem(last=False)
        if found is None:
            raise exceptions.NoCredentialsFor(tenant, self.name)
        return found

    def headers_for(self, request: OutgoingRequest) -> dict[str, str]:
        """The headers of the source of the tenant the request acts for — never another
        tenant's."""
        return self.source_of(request.tenant).headers_for(request)

    def invalidate(
        self,
        request: OutgoingRequest,
        presented: Mapping[str, str],
        answer: Mapping[str, str] | None = None,
    ) -> bool:
        """Asked of the source of the request's tenant."""
        return self.source_of(request.tenant).invalidate(request, presented, answer)

    def describe(self) -> dict[str, Any]:
        """How many tenants have a source in this process."""
        return {**super().describe(), "tenants": len(self._sources)}
