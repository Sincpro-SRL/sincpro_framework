"""`TokenExchange`: a token for another API acting for the caller in play — RFC 8693 — so the API
downstream knows who the call is for, and grants what that person may there.

    orders = TokenExchange("orders-api", client_id="billing", client_secret=secret,
                           issuer="https://auth.sincpro.com.bo/realms/sincpro")
    orders.headers_for(request)     # inside a call authenticated with a user's bearer token
        ->  {"authorization": "Bearer <a token for orders-api, for that user>"}

Context: the subject token is the bearer the caller presented — held by the entrypoint while its
call runs (`security.presented`), never on the identity. Each exchanged token is cached per
subject token's fingerprint — and per what is asked: the audience, the requested token type, the
actor — until shortly before it expires, and never past the subject token's own `exp` when it is a
JWT (read, not verified: it only shortens the cache). A subject token the server will not exchange
is the caller's to fix: `Unauthenticated` — log in again. That is `invalid_grant`, or
`invalid_request` whose description names the subject token (RFC 8693 §2.2.2 answers an invalid
subject token with `invalid_request`, which also means a malformed request: only the description
tells them apart). Anything else refused is this service's client: `CredentialsRejected`.
"""

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.client.adapters.cached_token_source import CachedTokenSource
from sincpro_framework.auth.client.adapters.token_cache import TokenCache
from sincpro_framework.auth.client.domain.outbound import (
    OutgoingRequest,
    TokenSource,
    parse_bearer_token,
)
from sincpro_framework.auth.client.domain.token_response import (
    TokenResponse,
    TokenTypeIdentifier,
)
from sincpro_framework.auth.common.adapters.server_metadata import OidcDiscovery
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.http import ClientAuthentication, HttpClient
from sincpro_framework.auth.common.infrastructure import jws
from sincpro_framework.auth.common.infrastructure.digests import short_digest
from sincpro_framework.auth.common.infrastructure.security_context import (
    current_presented_token,
)

if TYPE_CHECKING:
    from sincpro_framework.auth.client.adapters.dpop_prover import DpopProver

TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"


def get_caller_token(request: OutgoingRequest) -> tuple[str, str] | None:
    """The caller's bearer token, as an access token — `None` outside an authenticated call."""
    token = current_presented_token()
    return None if token is None else (token, TokenTypeIdentifier.ACCESS_TOKEN)


class TokenExchange(CachedTokenSource):
    """A token for `audience`, exchanged for the caller's own."""

    def __init__(
        self,
        audience: str,
        client: ClientAuthentication | None = None,
        client_id: str | None = None,
        client_secret: Any = None,
        token_endpoint: str | None = None,
        issuer: str | None = None,
        scope: Iterable[str] = (),
        resources: Iterable[str] = (),
        subject: Callable[[OutgoingRequest], tuple[str, str] | None] = get_caller_token,
        actor: TokenSource | None = None,
        requested_token_type: TokenTypeIdentifier = TokenTypeIdentifier.ACCESS_TOKEN,
        http: HttpClient | None = None,
        discovery: OidcDiscovery | None = None,
        cache: TokenCache | None = None,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
        timeout: timedelta = timedelta(seconds=10),
        name: str = "token-exchange",
        dpop: "DpopProver | None" = None,
    ) -> None:
        """`subject` answers the token to exchange and its type — the caller's bearer by default;
        `actor`, when given, says which service acts (its token sent as `actor_token`)."""
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
        self.subject = subject
        self.actor = actor
        self.requested_token_type = requested_token_type
        self.cache = cache or TokenCache(now=now)
        self.name = name

    def _subject_token(self, request: OutgoingRequest) -> tuple[str, str]:
        """The token acted for — `ProgrammingError` when there is none: a cron has no caller."""
        found = self.subject(request)
        if found is None:
            raise exceptions.ProgrammingError(
                f"{self.name} acts for the caller's token: none was presented"
            )
        return found

    def _cache_key(self, request: OutgoingRequest) -> tuple[Any, ...]:
        """Everything the exchanged token depends on — the subject token by its fingerprint."""
        return (
            "token_exchange",
            self.endpoint.url,
            self.client.client_id,
            self.audience,
            self.scope,
            self.resources,
            str(self.requested_token_type),
            (
                None
                if self.actor is None
                else json.dumps(self.actor.describe(), sort_keys=True, default=str)
            ),
            None if self.dpop is None else self.dpop.thumbprint,
            short_digest(self._subject_token(request)[0]),
        )

    def _cap_at_subject_expiry(
        self, issued: TokenResponse, subject_token: str
    ) -> TokenResponse:
        """`issued`, expiring no later than the subject token — its `exp` read when it is a JWT,
        not verified: it only shortens how long the exchanged token is cached."""
        if not jws.looks_like_jws(subject_token):
            return issued
        try:
            expires = jws.claim_datetime(jws.parse(subject_token).claims, "exp")
        except exceptions.Unauthenticated:
            return issued
        if expires is None or expires >= issued.expires_at:
            return issued
        return issued.model_copy(update={"expires_at": expires})

    def _fetch(self, request: OutgoingRequest) -> TokenResponse:
        """A token for the audience, acting for the subject of `request`.

        1. The subject token, the actor's token when there is an actor, and what is asked.
        2. The server will not exchange the subject token: `Unauthenticated`.
        Final: the token, cached no longer than the subject token lives.
        """
        token, kind = self._subject_token(request)
        fields = [
            ("grant_type", TOKEN_EXCHANGE),
            ("subject_token", token),
            ("subject_token_type", kind),
            ("audience", self.audience),
            ("requested_token_type", self.requested_token_type),
        ]
        if self.actor is not None:
            actor = parse_bearer_token(self.actor.headers_for(request))
            if actor is not None:
                fields.extend(
                    [
                        ("actor_token", actor),
                        ("actor_token_type", TokenTypeIdentifier.ACCESS_TOKEN),
                    ]
                )
        try:
            issued = self._request_token(fields)
        except exceptions.CredentialsRejected as refused:
            if isinstance(refused, exceptions.GrantRejected) or (
                refused.error == exceptions.OAuthErrorCode.INVALID_REQUEST
                and "subject" in refused.description.lower()
            ):
                raise exceptions.Unauthenticated(
                    "the caller's token cannot be exchanged"
                ) from refused
            raise
        return self._cap_at_subject_expiry(issued, token)

    def describe(self) -> dict[str, Any]:
        """The audience the token is exchanged for, and the client exchanging it."""
        return {
            **super().describe(),
            "audience": self.audience,
            "client_id": self.client.client_id,
        }
