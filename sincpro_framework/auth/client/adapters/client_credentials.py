"""`ClientCredentials`: this service's own token for an API, from an authorization server —
RFC 6749 §4.4 — fetched once, shared until shortly before it expires, fetched again when the API
refuses it.

    bank = ClientCredentials(
        client_id="billing", client_secret=settings.bank_secret,     ClientSecretBasic
        issuer="https://auth.bank.bo",                               the token endpoint, discovered
        scope=["payments"], audience="https://api.bank.bo",          RFC 8707: resources=[…]
    )
    ClientCredentials(client=PrivateKeyJwt("billing", key), token_endpoint="https://…/token")

Context: one source per API. The token is cached by `TokenCache` on the running context's store —
one fetch for every thread and replica — keyed by the endpoint, the client and what was asked,
never the secret.
"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.client.adapters.cached_token_source import CachedTokenSource
from sincpro_framework.auth.client.adapters.token_cache import TokenCache
from sincpro_framework.auth.client.domain.outbound import OutgoingRequest
from sincpro_framework.auth.client.domain.token_response import TokenResponse
from sincpro_framework.auth.common.adapters.server_metadata import OidcDiscovery
from sincpro_framework.auth.common.domain.http import ClientAuthentication, HttpClient

if TYPE_CHECKING:
    from sincpro_framework.auth.client.adapters.dpop_prover import DpopProver


class ClientCredentials(CachedTokenSource):
    """A client-credentials token for one API, cached and shared."""

    def __init__(
        self,
        client: ClientAuthentication | None = None,
        client_id: str | None = None,
        client_secret: Any = None,
        token_endpoint: str | None = None,
        issuer: str | None = None,
        scope: Iterable[str] = (),
        audience: str | None = None,
        resources: Iterable[str] = (),
        http: HttpClient | None = None,
        discovery: OidcDiscovery | None = None,
        cache: TokenCache | None = None,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
        timeout: timedelta = timedelta(seconds=10),
        name: str = "client-credentials",
        dpop: "DpopProver | None" = None,
    ) -> None:
        self._init_token_endpoint(
            client,
            client_id,
            client_secret,
            token_endpoint,
            issuer,
            http,
            discovery,
            now,
            timeout,
            scope,
            resources,
            dpop,
        )
        self.audience = audience
        self.cache = cache or TokenCache(now=now)
        self.name = name

    def _cache_key(self, request: OutgoingRequest) -> tuple[Any, ...]:
        """The grant's parameters — the same for every request — never the secret."""
        return (
            "client_credentials",
            self.endpoint.url,
            self.client.client_id,
            self.scope,
            self.audience,
            self.resources,
            None if self.dpop is None else self.dpop.thumbprint,
        )

    def _fetch(self, request: OutgoingRequest) -> TokenResponse:
        """A new token from the endpoint — for the audience, when there is one."""
        fields = [("grant_type", "client_credentials")]
        if self.audience:
            fields.append(("audience", self.audience))
        return self._request_token(fields)

    def describe(self) -> dict[str, Any]:
        """The client, the scope and the audience — never a secret."""
        return {
            **super().describe(),
            "client_id": self.client.client_id,
            "scope": list(self.scope),
            "audience": self.audience,
        }

    def __repr__(self) -> str:
        return f"ClientCredentials(client_id={self.client.client_id!r}, scope={list(self.scope)!r})"
