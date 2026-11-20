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

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.adapters.client_auth import ClientSecretBasic
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.http import ClientAuthentication, HttpClient
from sincpro_framework.auth.domain.outbound import OutgoingRequest, TokenSource
from sincpro_framework.auth.infrastructure.urllib_http import UrllibHttp
from sincpro_framework.auth.services.server_metadata import OidcDiscovery
from sincpro_framework.auth.services.token_cache import TokenCache, bearer_of
from sincpro_framework.auth.services.token_endpoint import IssuedToken, TokenEndpoint

if TYPE_CHECKING:
    from sincpro_framework.auth.adapters.outbound.dpop import DpopProver


def client_of(
    client: ClientAuthentication | None, client_id: str | None, client_secret: Any
) -> ClientAuthentication:
    """The client authentication given, else `ClientSecretBasic` of the id and secret."""
    if client is not None:
        return client
    if client_id is None or client_secret is None:
        raise exceptions.ProgrammingError("give client=, or client_id= and client_secret=")
    return ClientSecretBasic(client_id, client_secret)


def endpoint_of(
    token_endpoint: str | None, issuer: str | None, discovery: OidcDiscovery
) -> str | Callable[[], str]:
    """The token endpoint given, else a function discovering it from `issuer`."""
    if token_endpoint is not None:
        return token_endpoint
    if issuer is None:
        raise exceptions.ProgrammingError("give token_endpoint=, or issuer= to discover it")
    return lambda: discovery.of(issuer).endpoint("token_endpoint")


class ClientCredentials(TokenSource):
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
        self.client = client_of(client, client_id, client_secret)
        self.http: HttpClient = http or UrllibHttp()
        self.discovery = discovery or OidcDiscovery(http=self.http)
        self.endpoint = TokenEndpoint(
            endpoint_of(token_endpoint, issuer, self.discovery),
            self.client,
            self.http,
            now,
            timeout=timeout,
        )
        self.scope = tuple(sorted(scope))
        self.audience = audience
        self.resources = tuple(sorted(resources))
        self.cache = cache or TokenCache(now=now)
        self.name = name
        self.dpop = dpop

    def _key(self) -> tuple[Any, ...]:
        """What the token is cached by — the grant's parameters, never the secret."""
        return (
            "client_credentials",
            self.endpoint.url,
            self.client.client_id,
            self.scope,
            self.audience,
            self.resources,
            None if self.dpop is None else self.dpop.thumbprint,
        )

    def _fetched(self) -> IssuedToken:
        """A new token from the endpoint."""
        fields = [("grant_type", "client_credentials")]
        if self.scope:
            fields.append(("scope", " ".join(self.scope)))
        if self.audience:
            fields.append(("audience", self.audience))
        fields.extend(("resource", one) for one in self.resources)
        headers = (
            {} if self.dpop is None else {"dpop": self.dpop.proof("POST", self.endpoint.url)}
        )
        return self.endpoint.grant(fields, headers)

    def token(self) -> IssuedToken:
        """The current token — cached, fetched when there is none still good."""
        return self.cache.token(self._key(), self._fetched)

    def headers_for(self, request: OutgoingRequest) -> dict[str, str]:
        """`Bearer <token>` — or, for a DPoP-bound token, `DPoP <token>` with this request's
        proof (RFC 9449)."""
        issued = self.token()
        token = issued.access_token.get_secret_value()
        if self.dpop is not None and issued.token_type.lower() == "dpop":
            return {
                "authorization": f"DPoP {token}",
                "dpop": self.dpop.proof(request.method, request.url, token),
            }
        return {"authorization": f"Bearer {token}"}

    def invalidate(
        self,
        request: OutgoingRequest,
        presented: Mapping[str, str],
        answer: Mapping[str, str] | None = None,
    ) -> bool:
        if self.dpop is not None and answer and self.dpop.learn(request.url, answer):
            return True
        token = bearer_of(presented)
        return token is not None and self.cache.refused(self._key(), token)

    def describe(self) -> dict[str, Any]:
        return {
            **super().describe(),
            "client_id": self.client.client_id,
            "scope": list(self.scope),
            "audience": self.audience,
        }

    def __repr__(self) -> str:
        return f"ClientCredentials(client_id={self.client.client_id!r}, scope={list(self.scope)!r})"
