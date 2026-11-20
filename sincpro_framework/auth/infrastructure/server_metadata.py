"""What an authorization server publishes about itself — its keys, its token, introspection,
revocation and device endpoints — read once from its discovery document and cached.

    discovery = OidcDiscovery()
    discovery.of("https://auth.sincpro.com.bo/realms/sincpro").jwks_uri
        ->  "https://auth.sincpro.com.bo/realms/sincpro/protocol/openid-connect/certs"

Context: one component both halves share — `JwtProvider.from_issuer` finds the keys here,
`ClientCredentials(issuer=…)` the token endpoint. Every component built without `http=` nor
`discovery=` asks the process's own (`discovery_for`), so an issuer is discovered once per
process, not once per component.
OpenID Connect Discovery is tried first, RFC 8414 next. A document that names another issuer is
refused (a mix-up attack: a server answering for another), never trusted. The document is cached
an hour and, while the server is down, the last good one is served for a day (`FailSafe`) — an
identity provider restarting never refuses every token.
"""

import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import ConfigDict, Field

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.http import HttpClient, HttpRequest, safe_url
from sincpro_framework.auth.infrastructure.urllib_http import UrllibHttp
from sincpro_framework.data_layer.caching import (
    Cache,
    CachePolicy,
    FailSafe,
    JsonCodec,
    TimeToLive,
)
from sincpro_framework.exceptions import ServiceUnavailableError
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger


class ServerMetadata(DataTransferObject):
    """An authorization server's metadata — the fields auth reads, and the whole document."""

    model_config = ConfigDict(frozen=True)

    issuer: str
    jwks_uri: str | None = None
    token_endpoint: str | None = None
    introspection_endpoint: str | None = None
    revocation_endpoint: str | None = None
    device_authorization_endpoint: str | None = None
    userinfo_endpoint: str | None = None
    authorization_endpoint: str | None = None
    document: dict[str, Any] = Field(default_factory=dict)
    """Everything the server published, for what auth does not read itself."""

    @classmethod
    def of(cls, document: dict[str, Any]) -> "ServerMetadata":
        """The metadata out of a discovery document — the fields it names, and the document."""
        named = {name: document.get(name) for name in cls.model_fields if name != "document"}
        return cls(
            **{k: v for k, v in named.items() if isinstance(v, str)}, document=document
        )

    def endpoint(self, name: str) -> str:
        """The endpoint `name` (`token_endpoint`, `jwks_uri`…) — `ProgrammingError` when the
        server publishes none: what it was asked for, it does not do.

            metadata.endpoint("device_authorization_endpoint")
                ->  raises ProgrammingError("https://… publishes no device_authorization_endpoint")
        """
        found = getattr(self, name, None) or self.document.get(name)
        if not isinstance(found, str) or not found:
            raise exceptions.ProgrammingError(f"{self.issuer} publishes no {name}")
        return found


def discovery_urls(issuer: str) -> list[str]:
    """Where `issuer`'s metadata may be, in the order asked: OpenID Connect Discovery, then
    RFC 8414 — whose well-known part goes between the host and the issuer's path.

        discovery_urls("https://h/realms/a")
            ->  ["https://h/realms/a/.well-known/openid-configuration",
                 "https://h/.well-known/oauth-authorization-server/realms/a"]
    """
    parts = urlsplit(issuer)
    path = parts.path.rstrip("/")
    oidc = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    oauth = urlunsplit(
        (parts.scheme, parts.netloc, f"/.well-known/oauth-authorization-server{path}", "", "")
    )
    return [oidc, oauth]


class OidcDiscovery:
    """Issuers' metadata, fetched through `http` and cached."""

    def __init__(
        self,
        http: HttpClient | None = None,
        cache: Cache | None = None,
        ttl: timedelta = timedelta(hours=1),
        serve_stale_for: timedelta = timedelta(hours=24),
        timeout: timedelta = timedelta(seconds=10),
    ) -> None:
        """`http` is `UrllibHttp` unless given; `cache` holds the documents in this process
        unless one on a shared store is given; `ttl` is how long a document is believed and
        `serve_stale_for` how long past it the last good one stands in while the server is down.
        """
        self.http: HttpClient = http or UrllibHttp()
        self.cache = cache or Cache(namespace="auth-discovery")
        self.timeout = timeout
        self._policy: CachePolicy[ServerMetadata] = CachePolicy(
            freshness=TimeToLive(ttl=ttl),
            failure=FailSafe(serve_for=serve_stale_for, errors=ServiceUnavailableError),
        )
        self._codec = JsonCodec(ServerMetadata)

    def _fetched(self, issuer: str) -> ServerMetadata:
        """`issuer`'s metadata as its server answers it now.

        1. Each discovery URL in turn; a 404 tries the next.
        2. Another failure, or a body that is not a JSON object: `EndpointUnavailable`.
        3. A document whose `issuer` is not exactly `issuer`: logged as an error and refused
           with `EndpointUnavailable` — never used.
        Final: the metadata; `EndpointUnavailable` when no URL published any.
        """
        for url in discovery_urls(issuer):
            answer = self.http.send(
                HttpRequest(
                    url=url, headers={"accept": "application/json"}, timeout=self.timeout
                )
            )
            if answer.status == 404:
                continue
            if not answer.ok:
                raise exceptions.EndpointUnavailable(
                    safe_url(url),
                    f"answered {answer.status}",
                    answer.status,
                    answer.retry_after(),
                )
            document = answer.json_object()
            if not document:
                raise exceptions.EndpointUnavailable(safe_url(url), "answered no JSON object")
            if document.get("issuer") != issuer:
                logger.error(
                    "a discovery document names another issuer — refused",
                    url=safe_url(url),
                    expected=issuer,
                    named=str(document.get("issuer")),
                )
                raise exceptions.EndpointUnavailable(
                    safe_url(url), f"names issuer {document.get('issuer')!r}, not {issuer!r}"
                )
            return ServerMetadata.of(document)
        raise exceptions.EndpointUnavailable(issuer, "publishes no discovery document")

    def of(self, issuer: str) -> ServerMetadata:
        """`issuer`'s metadata — cached; fetched on the first call, never at construction.

        discovery.of("https://auth.sincpro.com.bo/realms/sincpro").token_endpoint
            ->  "https://auth.sincpro.com.bo/realms/sincpro/protocol/openid-connect/token"
        """
        return self.cache.get_or_compute(
            ("oidc-discovery", issuer),
            lambda: self._fetched(issuer),
            self._policy,
            self._codec,
        )

    def forget(self, issuer: str) -> None:
        """Drop `issuer`'s cached document — the next call fetches it again."""
        self.cache.forget(("oidc-discovery", issuer), self._codec)


_PROCESS_DISCOVERY: list[OidcDiscovery] = []
_PROCESS_DISCOVERY_LOCK = threading.Lock()


def discovery_for(http: HttpClient | None, discovery: OidcDiscovery | None) -> OidcDiscovery:
    """The discovery a component asks: `discovery` when given; one through `http` when only that
    was — a test's `ScriptedHttp`, a project's own client; else the process's, made on first use
    and shared by every component built on the defaults.

        discovery_for(None, None) is discovery_for(None, None)          ->  True
        discovery_for(ScriptedHttp(...), None).http                     ->  that ScriptedHttp
    """
    if discovery is not None:
        return discovery
    if http is not None:
        return OidcDiscovery(http=http)
    with _PROCESS_DISCOVERY_LOCK:
        if not _PROCESS_DISCOVERY:
            _PROCESS_DISCOVERY.append(OidcDiscovery())
        return _PROCESS_DISCOVERY[0]


def located(
    endpoint: str | None, issuer: str | None, discovery: OidcDiscovery, name: str
) -> str | Callable[[], str]:
    """The endpoint given; else a function discovering `name` from `issuer` on its first call —
    never at construction; `ProgrammingError` with neither.

        located(None, KEYCLOAK, discovery, "token_endpoint")()
            ->  "https://auth.sincpro.com.bo/realms/sincpro/protocol/openid-connect/token"
    """
    if endpoint is not None:
        return endpoint
    if issuer is None:
        raise exceptions.ProgrammingError(f"no {name} given, and no issuer= to discover it")
    return lambda: discovery.of(issuer).endpoint(name)
