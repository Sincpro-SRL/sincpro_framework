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

from sincpro_framework.auth.client.adapters.client_authentication import ClientSecretBasic
from sincpro_framework.auth.client.domain.outbound import OutgoingRequest, TokenSource
from sincpro_framework.auth.client.infrastructure.token_cache import TokenCache, bearer_of
from sincpro_framework.auth.client.infrastructure.token_endpoint import (
    IssuedToken,
    TokenEndpoint,
)
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.http import ClientAuthentication, HttpClient
from sincpro_framework.auth.infrastructure.server_metadata import (
    OidcDiscovery,
    discovery_for,
    located,
)
from sincpro_framework.auth.infrastructure.urllib_http import UrllibHttp

if TYPE_CHECKING:
    from sincpro_framework.auth.client.adapters.dpop_prover import DpopProver


def client_of(
    client: ClientAuthentication | None, client_id: str | None, client_secret: Any
) -> ClientAuthentication:
    """The client authentication given, else `ClientSecretBasic` of the id and secret."""
    if client is not None:
        return client
    if client_id is None or client_secret is None:
        raise exceptions.ProgrammingError("give client=, or client_id= and client_secret=")
    return ClientSecretBasic(client_id, client_secret)


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
        self.discovery = discovery_for(http, discovery)
        self.http: HttpClient = http or UrllibHttp()
        self.endpoint = TokenEndpoint(
            located(token_endpoint, issuer, self.discovery, "token_endpoint"),
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

    def _proved(self, dpop: "DpopProver", fields: list[tuple[str, str]]) -> IssuedToken:
        """A DPoP-bound token: asked with a proof of `dpop`'s key — and asked once more, with
        the nonce the server answered, when it demands one (RFC 9449 §8, `use_dpop_nonce`)."""
        url = self.endpoint.url
        try:
            return self.endpoint.grant(fields, {"dpop": dpop.proof("POST", url)})
        except exceptions.CredentialsRejected as refused:
            if refused.error != "use_dpop_nonce" or not refused.dpop_nonce:
                raise
            dpop.learn(url, {"dpop-nonce": refused.dpop_nonce})
            return self.endpoint.grant(fields, {"dpop": dpop.proof("POST", url)})

    def _fetched(self) -> IssuedToken:
        """A new token from the endpoint — proving the client's key when it holds one."""
        fields = [("grant_type", "client_credentials")]
        if self.scope:
            fields.append(("scope", " ".join(self.scope)))
        if self.audience:
            fields.append(("audience", self.audience))
        fields.extend(("resource", one) for one in self.resources)
        if self.dpop is not None:
            return self._proved(self.dpop, fields)
        return self.endpoint.grant(fields)

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
        """Whether asking again may succeed: a 401 bringing a new DPoP nonce — now learned; or
        the token refused dropped from the cache (`TokenCache.refused`)."""
        if self.dpop is not None and answer and self.dpop.learn(request.url, answer):
            return True
        token = bearer_of(presented)
        return token is not None and self.cache.refused(self._key(), token)

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
