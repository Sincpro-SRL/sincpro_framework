"""The clients an authorization server knows — where its answer may go, which grants and scopes
a client may use, and whether it proves itself with a secret — and the registry that holds them.

    OAuthClient(client_id="cl_9f…", redirect_uris=("https://claude.ai/api/mcp/auth_callback",))
        .allows_redirect_uri("https://claude.ai/api/mcp/auth_callback")    ->  True
        .allows_redirect_uri("https://claude.ai/api/mcp/auth_callback/")   ->  False: exact
        .allows_redirect_uri("http://127.0.0.1:53123/cb")  ->  True when it registered
                                                               http://127.0.0.1/cb: any port

Context: a public client — a browser app, a CLI, an MCP host — holds no secret and proves the
request is its own by PKCE alone; a confidential one proves itself with a secret, stored here
by its digest only. Redirects compare exactly (OAuth 2.1 §4.1.1), except the port of a loopback
redirect: a desktop app listens on whatever port the system gives it (RFC 8252 §7.3).
"""

import ipaddress
from abc import ABC, abstractmethod
from collections.abc import Iterable
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject


class GrantType(StrEnum):
    """How a client obtains tokens — the grants this authorization server knows."""

    AUTHORIZATION_CODE = "authorization_code"
    REFRESH_TOKEN = "refresh_token"


class ClientAuthMethod(StrEnum):
    """How a client proves itself at the token endpoint — RFC 7591 §2."""

    NONE = "none"
    """A public client: no secret, PKCE proves its requests."""
    CLIENT_SECRET_BASIC = "client_secret_basic"
    """The secret in the `Authorization: Basic` header — RFC 6749 §2.3.1, every server's."""
    CLIENT_SECRET_POST = "client_secret_post"
    """The secret as `client_secret` in the form body."""


def is_loopback_host(host: str) -> bool:
    """Whether `host` names this machine — `localhost` or a loopback address."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_registrable_redirect_uri(uri: str) -> bool:
    """Whether `uri` may be a client's redirect — https; http to a loopback address (a desktop
    app, RFC 8252 §7.3); a private-use scheme with a dot (a mobile app, RFC 8252 §7.1); never a
    fragment (RFC 6749 §3.1.2) nor user information."""
    parts = urlsplit(uri)
    if "#" in uri or not parts.scheme or parts.username is not None:
        return False
    if parts.scheme == "https":
        return bool(parts.hostname)
    if parts.scheme == "http":
        return is_loopback_host(parts.hostname or "")
    return "." in parts.scheme


class OAuthClient(DataTransferObject):
    """A client of the authorization server, as it was registered."""

    model_config = ConfigDict(frozen=True)

    client_id: str
    redirect_uris: tuple[str, ...]
    name: str = ""
    grant_types: frozenset[GrantType] = frozenset(
        {GrantType.AUTHORIZATION_CODE, GrantType.REFRESH_TOKEN}
    )
    scopes: frozenset[str] = frozenset()
    """The scopes it may ask for — any when empty."""
    secret_digest: str | None = None
    """The digest of a confidential client's secret — `None` for a public client."""
    token_endpoint_auth_method: ClientAuthMethod | None = None
    """How it registered to prove itself at the token endpoint — answered back as it asked
    (RFC 7591 §2, #340); `None` for a client of 3.41, read from whether it holds a secret."""
    tenant: str | None = None
    """The tenant it belongs to — `None`, every tenant's: only sign-ins of its tenant are
    issued to it (#339)."""

    @property
    def is_public(self) -> bool:
        """Whether it holds no secret: PKCE alone proves its requests."""
        return self.secret_digest is None

    @property
    def auth_method(self) -> ClientAuthMethod:
        """How it proves itself at the token endpoint — as it registered; `client_secret_basic`
        with a secret when it said nothing."""
        if self.token_endpoint_auth_method is not None:
            return self.token_endpoint_auth_method
        return (
            ClientAuthMethod.NONE if self.is_public else ClientAuthMethod.CLIENT_SECRET_BASIC
        )

    def allows_redirect_uri(self, uri: str) -> bool:
        """Whether `uri` is one it registered — compared exactly, never by prefix; for an
        `http` loopback redirect, whatever its port (RFC 8252 §7.3)."""
        if uri in self.redirect_uris:
            return True
        asked = urlsplit(uri)
        if asked.scheme != "http" or not is_loopback_host(asked.hostname or ""):
            return False
        for registered in self.redirect_uris:
            known = urlsplit(registered)
            if (
                known.scheme == "http"
                and known.hostname == asked.hostname
                and known.path == asked.path
                and known.query == asked.query
            ):
                return True
        return False

    def allows_grant(self, grant: GrantType) -> bool:
        """Whether it was registered for `grant`."""
        return grant in self.grant_types

    def allows_scopes(self, scopes: Iterable[str]) -> bool:
        """Whether every one of `scopes` is one it may ask for."""
        return not self.scopes or frozenset(scopes) <= self.scopes


class ClientRegistry(ABC):
    """The port: the clients by their id."""

    supports_metadata_documents: bool = False
    """Whether a `client_id` may be the URL of the client's metadata document (CIMD, #338) —
    published in the server's metadata as `client_id_metadata_document_supported`."""

    @abstractmethod
    def find(self, client_id: str) -> OAuthClient | None:
        """The client registered under `client_id` — `None` when there is none."""

    @abstractmethod
    def save(self, client: OAuthClient) -> None:
        """Register `client`, or replace the one under its id."""
