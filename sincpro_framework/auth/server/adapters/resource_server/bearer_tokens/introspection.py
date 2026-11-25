"""`IntrospectionProvider`: an opaque bearer token asked of the authorization server that issued
it — RFC 7662 — and the answer turned into an `Identity` the way `JwtProvider` reads a JWT.

    IntrospectionProvider(
        client=ClientSecretBasic("billing-api", settings.client_secret),
        issuer="https://auth.sincpro.com.bo/realms/sincpro",     the endpoint by discovery
        audience="billing-api",
        mapping=ClaimMapping.keycloak(client="billing-api"),
    )

Context: what an opaque token is can only be asked, so every answer is cached — by the token's
SHA-256, never the token — for `cache_for` and never past the token's own `exp`; an inactive
token's answer for `inactive_cache_ttl`, so a stream of bad tokens does not become a stream of
requests. A JWT is left to `JwtProvider` unless `skip_jwts=False`. The endpoint refusing this
service's client (401, 403) is its misconfiguration, never the caller's: `EndpointUnavailable`
(503), logged as an error.
"""

import time
from collections.abc import Callable, Iterable, Sequence
from datetime import timedelta
from typing import Any

from sincpro_framework.auth.common.adapters.server_metadata import (
    OidcDiscovery,
    resolve_discovery,
    resolve_endpoint,
)
from sincpro_framework.auth.common.adapters.urllib_http import UrllibHttp
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.http import (
    ClientAuthentication,
    HttpClient,
    HttpRequest,
)
from sincpro_framework.auth.common.domain.identity import Credentials, Identity
from sincpro_framework.auth.common.domain.provider import AuthProvider
from sincpro_framework.auth.common.domain.resolver import IdentityResolver, resolved
from sincpro_framework.auth.common.domain.revocations import (
    Revocations,
    related_subjects,
)
from sincpro_framework.auth.common.infrastructure import jws
from sincpro_framework.auth.common.infrastructure.claim_mapping import ClaimMapping
from sincpro_framework.auth.common.infrastructure.digests import digest
from sincpro_framework.auth.common.infrastructure.permission_versions import (
    cached_permission_versions,
)
from sincpro_framework.data_layer.caching import Cache, CachePolicy, JsonCodec, TimeToLive
from sincpro_framework.sincpro_logger import logger


class IntrospectionProvider(AuthProvider):
    """Opaque bearer tokens, introspected and cached by digest."""

    def __init__(
        self,
        client: ClientAuthentication,
        endpoint: str | None = None,
        issuer: str | None = None,
        audience: str | Iterable[str] | None = None,
        mapping: ClaimMapping | None = None,
        resolvers: Sequence[IdentityResolver] = (),
        revocations: Revocations | None = None,
        http: HttpClient | None = None,
        discovery: OidcDiscovery | None = None,
        cache: Cache | None = None,
        cache_for: timedelta = timedelta(seconds=60),
        inactive_cache_ttl: timedelta = timedelta(seconds=10),
        skip_jwts: bool = True,
        name: str = "introspection",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`endpoint`, or `issuer` to discover it on the first token — one of them, or
        `ProgrammingError`; `audience`, when given, must be named by the answer's `aud`."""
        self.client = client
        self.issuer = issuer
        self.audiences = (
            None
            if audience is None
            else frozenset({audience}) if isinstance(audience, str) else frozenset(audience)
        )
        self.mapping = mapping or ClaimMapping()
        self.resolvers = tuple(resolvers)
        self.revocations = revocations
        self.discovery = resolve_discovery(http, discovery)
        self.http: HttpClient = http or UrllibHttp()
        self._endpoint = resolve_endpoint(
            endpoint, issuer, self.discovery, "introspection_endpoint"
        )
        self.cache = cache or Cache(namespace="auth-introspection")
        self.inactive_cache_ttl = inactive_cache_ttl
        self.skip_jwts = skip_jwts
        self.name = name
        self.clock = clock
        self._policy: CachePolicy[dict[str, Any]] = CachePolicy(
            freshness=TimeToLive(ttl=cache_for)
        )
        self._codec = JsonCodec(dict[str, Any])

    @property
    def endpoint(self) -> str:
        """The introspection endpoint — given, or discovered from the issuer on first use."""
        return self._endpoint() if callable(self._endpoint) else self._endpoint

    def security_scheme(self) -> dict[str, Any] | None:
        """HTTP bearer, an opaque token."""
        return {"type": "http", "scheme": "bearer"}

    def _introspect(self, token: str) -> dict[str, Any]:
        """The endpoint's answer for `token`, with the moment it was asked.

        1. POSTed with the client's authentication and `token_type_hint=access_token`.
        2. 401 or 403: this service's client is refused — logged, `EndpointUnavailable`.
        3. Another failure, or an answer without a boolean `active`: `EndpointUnavailable`.
        Final: the answer and `checked_at`.
        """
        endpoint = self.endpoint
        fields, headers = self.client.apply(
            [("token", token), ("token_type_hint", "access_token")], endpoint
        )
        answer = self.http.send(HttpRequest.form(endpoint, fields, headers))
        if answer.status in (401, 403):
            logger.error(
                "the introspection endpoint refused this service's client",
                endpoint=HttpRequest(url=endpoint).target,
                client=self.client.client_id,
            )
            raise exceptions.EndpointUnavailable(
                HttpRequest(url=endpoint).target,
                "refused this service's client",
                answer.status,
            )
        if not answer.ok:
            raise exceptions.EndpointUnavailable(
                HttpRequest(url=endpoint).target,
                f"answered {answer.status}",
                answer.status,
                answer.retry_after(),
            )
        response = answer.json_object()
        if not isinstance(response.get("active"), bool):
            raise exceptions.EndpointUnavailable(
                HttpRequest(url=endpoint).target, "answered no active"
            )
        return {**response, "checked_at": self.clock()}

    def _cached_introspection(self, token: str) -> dict[str, Any]:
        """The cached answer for `token` — asked again once an inactive answer is older than
        `inactive_cache_ttl`."""
        key = ("introspection", digest(token))
        response = self.cache.get_or_compute(
            key, lambda: self._introspect(token), self._policy, self._codec
        )
        if (
            not response["active"]
            and self.clock() - response["checked_at"]
            > self.inactive_cache_ttl.total_seconds()
        ):
            self.cache.forget(key, self._codec)
            response = self.cache.get_or_compute(
                key, lambda: self._introspect(token), self._policy, self._codec
            )
        return response

    def _validate_response(self, response: dict[str, Any]) -> None:
        """`Unauthenticated` for an inactive token, or one whose issuer, audience or expiry is
        not what this service accepts — the expiry checked on every call, cached or not."""
        if not response["active"]:
            raise exceptions.Unauthenticated("token is not active")
        if self.issuer is not None and response.get("iss", self.issuer) != self.issuer:
            raise exceptions.Unauthenticated("token is from another issuer")
        if self.audiences is not None and not jws.audiences_of(response) & self.audiences:
            raise exceptions.Unauthenticated("token is for another audience")
        expires = jws.numeric_date(response, "exp")
        if expires is not None and self.clock() >= expires:
            raise exceptions.Unauthenticated("token expired")

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """Who the opaque bearer token says is calling.

        1. No bearer — or a JWT, unless `skip_jwts=False`: `None`.
        2. The answer, cached by digest; checked (`_validate_response`).
        3. Mapped to an identity; refused when revoked.
        Final: through every resolver.
        """
        token = credentials.bearer
        if token is None or (self.skip_jwts and jws.looks_like_jws(token)):
            return None
        response = self._cached_introspection(token)
        self._validate_response(response)
        claims = {name: value for name, value in response.items() if name != "checked_at"}
        identity = self.mapping.identity(claims, provider=self.name)
        with cached_permission_versions():
            if self.revocations is not None:
                state = self.revocations.state(
                    identity.subject,
                    identity.token_id,
                    identity.session,
                    related_subjects(identity),
                )
                refusal = state.revocation_reason(jws.claim_datetime(claims, "iat"))
                if refusal is not None:
                    raise exceptions.Unauthenticated(refusal)
            return resolved(identity, self.resolvers)
