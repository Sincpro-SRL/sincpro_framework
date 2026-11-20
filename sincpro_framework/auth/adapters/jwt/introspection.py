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
token's answer for `inactive_for`, so a stream of bad tokens does not become a stream of
requests. A JWT is left to `JwtProvider` unless `skip_jwts=False`. The endpoint refusing this
service's client (401, 403) is its misconfiguration, never the caller's: `EndpointUnavailable`
(503), logged as an error.
"""

import time
from collections.abc import Callable, Iterable, Sequence
from datetime import timedelta
from typing import Any

from sincpro_framework.auth.adapters.jwt.mapping import ClaimMapping
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.http import ClientAuthentication, HttpClient, HttpRequest
from sincpro_framework.auth.domain.identity import Credentials, Identity
from sincpro_framework.auth.domain.provider import AuthProvider
from sincpro_framework.auth.domain.resolver import IdentityResolver, resolved
from sincpro_framework.auth.domain.revocations import Revocations
from sincpro_framework.auth.infrastructure import jws
from sincpro_framework.auth.infrastructure.digests import digest
from sincpro_framework.auth.infrastructure.urllib_http import UrllibHttp
from sincpro_framework.auth.services.server_metadata import OidcDiscovery
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
        inactive_for: timedelta = timedelta(seconds=10),
        skip_jwts: bool = True,
        name: str = "introspection",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`endpoint`, or `issuer` to discover it on the first token — one of them, or
        `ProgrammingError`; `audience`, when given, must be named by the answer's `aud`."""
        if endpoint is None and issuer is None:
            raise exceptions.ProgrammingError(
                "IntrospectionProvider needs endpoint= or issuer="
            )
        self.client = client
        self._endpoint = endpoint
        self.issuer = issuer
        self.audiences = (
            None
            if audience is None
            else frozenset({audience}) if isinstance(audience, str) else frozenset(audience)
        )
        self.mapping = mapping or ClaimMapping()
        self.resolvers = tuple(resolvers)
        self.revocations = revocations
        self.http: HttpClient = http or UrllibHttp()
        self.discovery = discovery or OidcDiscovery(http=self.http)
        self.cache = cache or Cache(namespace="auth-introspection")
        self.inactive_for = inactive_for
        self.skip_jwts = skip_jwts
        self.name = name
        self.clock = clock
        self._policy: CachePolicy[dict[str, Any]] = CachePolicy(
            freshness=TimeToLive(ttl=cache_for)
        )
        self._codec = JsonCodec(dict[str, Any])

    @property
    def endpoint(self) -> str:
        """The introspection endpoint — discovered from the issuer on first use."""
        if self._endpoint is None:
            assert self.issuer is not None
            self._endpoint = self.discovery.of(self.issuer).endpoint("introspection_endpoint")
        return self._endpoint

    def security_scheme(self) -> dict[str, Any] | None:
        """HTTP bearer, an opaque token."""
        return {"type": "http", "scheme": "bearer"}

    def _asked(self, token: str) -> dict[str, Any]:
        """The endpoint's answer for `token`, with the moment it was asked.

        1. POSTed with the client's authentication and `token_type_hint=access_token`.
        2. 401 or 403: this service's client is refused — logged, `EndpointUnavailable`.
        3. Another failure, or an answer without a boolean `active`: `EndpointUnavailable`.
        Final: the answer and `checked_at`.
        """
        endpoint = self.endpoint
        fields, headers = self.client.applied(
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
        said = answer.json_object()
        if not isinstance(said.get("active"), bool):
            raise exceptions.EndpointUnavailable(
                HttpRequest(url=endpoint).target, "answered no active"
            )
        return {**said, "checked_at": self.clock()}

    def _answer(self, token: str) -> dict[str, Any]:
        """The cached answer for `token` — asked again once an inactive answer is older than
        `inactive_for`."""
        key = ("introspection", digest(token))
        said = self.cache.get_or_compute(
            key, lambda: self._asked(token), self._policy, self._codec
        )
        if (
            not said["active"]
            and self.clock() - said["checked_at"] > self.inactive_for.total_seconds()
        ):
            self.cache.forget(key, self._codec)
            said = self.cache.get_or_compute(
                key, lambda: self._asked(token), self._policy, self._codec
            )
        return said

    def _checked(self, said: dict[str, Any]) -> None:
        """`Unauthenticated` for an inactive token, or one whose issuer, audience or expiry is
        not what this service accepts — the expiry checked on every call, cached or not."""
        if not said["active"]:
            raise exceptions.Unauthenticated("token is not active")
        if self.issuer is not None and said.get("iss", self.issuer) != self.issuer:
            raise exceptions.Unauthenticated("token is from another issuer")
        if self.audiences is not None and not jws.audiences_of(said) & self.audiences:
            raise exceptions.Unauthenticated("token is for another audience")
        expires = jws.numeric_date(said, "exp")
        if expires is not None and self.clock() >= expires:
            raise exceptions.Unauthenticated("token expired")

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """Who the opaque bearer token says is calling.

        1. No bearer — or a JWT, unless `skip_jwts=False`: `None`.
        2. The answer, cached by digest; checked (`_checked`).
        3. Mapped to an identity; refused when revoked.
        Final: through every resolver.
        """
        token = credentials.bearer
        if token is None or (self.skip_jwts and jws.looks_like_jws(token)):
            return None
        said = self._answer(token)
        self._checked(said)
        claims = {name: value for name, value in said.items() if name != "checked_at"}
        identity = self.mapping.identity(claims, provider=self.name)
        if self.revocations is not None:
            state = self.revocations.state(
                identity.subject, identity.token_id, identity.session
            )
            refusal = state.refusal(jws.moment(claims, "iat"))
            if refusal is not None:
                raise exceptions.Unauthenticated(refusal)
        return resolved(identity, self.resolvers)
