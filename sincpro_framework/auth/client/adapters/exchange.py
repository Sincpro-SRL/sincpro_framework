"""`TokenExchange`: a token for another API acting for the caller in play — RFC 8693 — so the API
downstream knows who the call is for, and grants what that person may there.

    orders = TokenExchange(ClientSecretBasic("billing", secret), audience="orders-api",
                           issuer="https://auth.sincpro.com.bo/realms/sincpro")
    orders.headers_for(request)     # inside a call authenticated with a user's bearer token
        ->  {"authorization": "Bearer <a token for orders-api, for that user>"}

Context: the subject token is the bearer the caller presented — held by the entrypoint while its
call runs (`security.presented`), never on the identity. Each exchanged token is cached per
subject token's fingerprint, until shortly before it expires. A subject token the server will not
exchange is the caller's to fix: `Unauthenticated` — log in again; this service's client refused
is `CredentialsRejected`.
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sincpro_framework.auth.client.domain.outbound import OutgoingRequest, TokenSource
from sincpro_framework.auth.client.infrastructure.token_cache import TokenCache, bearer_of
from sincpro_framework.auth.client.infrastructure.token_endpoint import (
    IssuedToken,
    TokenEndpoint,
)
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.http import ClientAuthentication, HttpClient
from sincpro_framework.auth.infrastructure.digests import fingerprint
from sincpro_framework.auth.infrastructure.security_context import presented_credential
from sincpro_framework.auth.infrastructure.server_metadata import (
    OidcDiscovery,
    discovery_for,
    located,
)
from sincpro_framework.auth.infrastructure.urllib_http import UrllibHttp

TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"


def presented(request: OutgoingRequest) -> tuple[str, str] | None:
    """The caller's bearer token, as an access token — `None` outside an authenticated call."""
    token = presented_credential()
    return None if token is None else (token, ACCESS_TOKEN)


class TokenExchange(TokenSource):
    """A token for `audience`, exchanged for the caller's own."""

    def __init__(
        self,
        client: ClientAuthentication,
        audience: str,
        token_endpoint: str | None = None,
        issuer: str | None = None,
        scope: Iterable[str] = (),
        resources: Iterable[str] = (),
        subject: Callable[[OutgoingRequest], tuple[str, str] | None] = presented,
        actor: TokenSource | None = None,
        requested_token_type: str = ACCESS_TOKEN,
        http: HttpClient | None = None,
        discovery: OidcDiscovery | None = None,
        cache: TokenCache | None = None,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
        name: str = "token-exchange",
    ) -> None:
        """`subject` answers the token to exchange and its type — the caller's bearer by default;
        `actor`, when given, says which service acts (its token sent as `actor_token`)."""
        self.client = client
        self.audience = audience
        self.discovery = discovery_for(http, discovery)
        self.http: HttpClient = http or UrllibHttp()
        self.endpoint = TokenEndpoint(
            located(token_endpoint, issuer, self.discovery, "token_endpoint"),
            client,
            self.http,
            now,
        )
        self.scope = tuple(sorted(scope))
        self.resources = tuple(sorted(resources))
        self.subject = subject
        self.actor = actor
        self.requested_token_type = requested_token_type
        self.cache = cache or TokenCache(now=now)
        self.name = name

    def _subject(self, request: OutgoingRequest) -> tuple[str, str]:
        """The token acted for — `ProgrammingError` when there is none: a cron has no caller."""
        found = self.subject(request)
        if found is None:
            raise exceptions.ProgrammingError(
                f"{self.name} acts for the caller's token: none was presented"
            )
        return found

    def _key(self, subject_token: str) -> tuple[Any, ...]:
        return (
            "token_exchange",
            self.endpoint.url,
            self.client.client_id,
            self.audience,
            self.scope,
            self.resources,
            fingerprint(subject_token),
        )

    def _exchanged(self, request: OutgoingRequest, subject: tuple[str, str]) -> IssuedToken:
        """A token for the audience, acting for `subject` — `Unauthenticated` when the server
        will not exchange the caller's token."""
        token, kind = subject
        fields = [
            ("grant_type", TOKEN_EXCHANGE),
            ("subject_token", token),
            ("subject_token_type", kind),
            ("audience", self.audience),
            ("requested_token_type", self.requested_token_type),
        ]
        if self.scope:
            fields.append(("scope", " ".join(self.scope)))
        fields.extend(("resource", one) for one in self.resources)
        if self.actor is not None:
            actor = bearer_of(self.actor.headers_for(request))
            if actor is not None:
                fields.extend([("actor_token", actor), ("actor_token_type", ACCESS_TOKEN)])
        try:
            return self.endpoint.grant(fields)
        except exceptions.GrantRejected as refused:
            raise exceptions.Unauthenticated(
                "the caller's token cannot be exchanged"
            ) from refused

    def headers_for(self, request: OutgoingRequest) -> dict[str, str]:
        """`Bearer` with a token exchanged for the subject this call acts for — cached per
        subject until it is about to expire."""
        subject = self._subject(request)
        issued = self.cache.token(
            self._key(subject[0]), lambda: self._exchanged(request, subject)
        )
        return {"authorization": f"Bearer {issued.access_token.get_secret_value()}"}

    def invalidate(
        self,
        request: OutgoingRequest,
        presented: Mapping[str, str],
        answer: Mapping[str, str] | None = None,
    ) -> bool:
        """The token exchanged for this call's subject dropped when it is the one refused —
        then asking again exchanges a new one."""
        token, subject = bearer_of(presented), self.subject(request)
        if token is None or subject is None:
            return False
        return self.cache.refused(self._key(subject[0]), token)

    def describe(self) -> dict[str, Any]:
        """The audience the token is exchanged for, and the client exchanging it."""
        return {
            **super().describe(),
            "audience": self.audience,
            "client_id": self.client.client_id,
        }
