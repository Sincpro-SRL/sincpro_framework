"""`ClientRegistration`: a client registering itself — RFC 7591 dynamic registration, what an MCP
host does the first time it connects.

    registered = server.register(["https://claude.ai/api/mcp/auth_callback"], name="Claude")
    registered.client.client_id     ->  "cl_9f…"
    registered.secret               ->  None: a public client, PKCE alone

    registered = server.register_from_metadata(request_json, initial_access_token)
    return JSONResponse(registered.answer(), status_code=201)

Context: a redirect is `https`, a loopback `http` (a desktop app, RFC 8252 §7.3), or a private-use
scheme with a dot (`com.example.app:/callback`, RFC 8252 §7.1); never a fragment, never user
information (`https://user@host`). A confidential client's secret is shown once, here, and stored
by its digest. Registration is open unless the server was given a `registration_token` — the
initial access token (RFC 7591 §3) a registration must then present.
"""

import hmac
import time
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

from pydantic import ConfigDict

from sincpro_framework.auth.common.domain.exceptions import OAuthError, OAuthErrorCode
from sincpro_framework.auth.common.infrastructure.digests import digest, random_token
from sincpro_framework.auth.server.domain.authorization_server.clients import (
    ClientAuthMethod,
    GrantType,
    OAuthClient,
    is_loopback_host,
)
from sincpro_framework.auth.server.domain.authorization_server.endpoints import (
    AuthorizationServerEndpoint,
)
from sincpro_framework.auth.server.services.authorization_server.core import (
    AuthorizationServerCore,
)
from sincpro_framework.sincpro_abstractions import DataTransferObject


class RegisteredClient(DataTransferObject):
    """A client just registered, and its secret — shown once, `None` for a public client."""

    model_config = ConfigDict(frozen=True)

    client: OAuthClient
    secret: str | None = None
    issued_at: int
    """When its id was issued, in seconds since the epoch — `client_id_issued_at`."""

    def answer(self) -> dict[str, Any]:
        """The registration response a client is sent (RFC 7591 §3.2.1) — the one place the
        secret is revealed."""
        response: dict[str, Any] = {
            "client_id": self.client.client_id,
            "client_id_issued_at": self.issued_at,
            "client_name": self.client.name,
            "redirect_uris": list(self.client.redirect_uris),
            "grant_types": sorted(one.value for one in self.client.grant_types),
            "response_types": ["code"],
            "token_endpoint_auth_method": self.client.auth_method.value,
        }
        if self.client.scopes:
            response["scope"] = " ".join(sorted(self.client.scopes))
        if self.secret is not None:
            response["client_secret"] = self.secret
            response["client_secret_expires_at"] = 0
        return response


class ClientRegistration(AuthorizationServerCore):
    """Dynamic client registration."""

    def is_registrable_redirect_uri(self, uri: str) -> bool:
        """Whether `uri` may be registered as a redirect — https; http to a loopback address (a
        desktop app, RFC 8252 §7.3); a private-use scheme with a dot (a mobile app, RFC 8252
        §7.1); never a fragment (RFC 6749 §3.1.2) nor user information. Override to accept
        others."""
        parts = urlsplit(uri)
        if "#" in uri or not parts.scheme or parts.username is not None:
            return False
        if parts.scheme == "https":
            return bool(parts.hostname)
        if parts.scheme == "http":
            return is_loopback_host(parts.hostname or "")
        return "." in parts.scheme

    def _validate_initial_access_token(self, initial_access_token: str | None) -> None:
        """The initial access token the server asks for, when it asks — `invalid_token` (401)."""
        if self.registration_token is not None and not hmac.compare_digest(
            initial_access_token or "", self.registration_token
        ):
            raise OAuthError(
                OAuthErrorCode.INVALID_TOKEN,
                "registration needs a valid initial access token",
            )

    def register(
        self,
        redirect_uris: Iterable[str],
        name: str = "",
        grant_types: Iterable[GrantType] = (
            GrantType.AUTHORIZATION_CODE,
            GrantType.REFRESH_TOKEN,
        ),
        scopes: Iterable[str] = (),
        confidential: bool = False,
        initial_access_token: str | None = None,
    ) -> RegisteredClient:
        """A new client — `invalid_redirect_uri` for none or one that may not be a redirect,
        `invalid_token` without the initial access token the server asks for."""
        self._validate_initial_access_token(initial_access_token)
        uris = tuple(redirect_uris)
        if not uris or not all(self.is_registrable_redirect_uri(one) for one in uris):
            raise OAuthError(
                OAuthErrorCode.INVALID_REDIRECT_URI,
                "redirect_uris are https, loopback http or a private-use scheme, with no "
                "fragment and no user information",
            )
        secret = random_token("cs_") if confidential else None
        client = OAuthClient(
            client_id=random_token("cl_", 16),
            redirect_uris=uris,
            name=name,
            grant_types=frozenset(grant_types),
            scopes=frozenset(scopes),
            secret_digest=digest(secret) if secret else None,
        )
        self.client_registry.save(client)
        return RegisteredClient(client=client, secret=secret, issued_at=int(time.time()))

    def register_from_metadata(
        self, metadata: Mapping[str, Any], initial_access_token: str | None = None
    ) -> RegisteredClient:
        """`register` from the client metadata a registration request posts (RFC 7591 §2) —
        `redirect_uris`, `client_name`, `grant_types`, `scope`, `token_endpoint_auth_method`.
        A value of the wrong shape, or a grant or method the server does not serve, is
        `invalid_client_metadata`."""
        redirect_uris = metadata.get("redirect_uris")
        grant_types = metadata.get(
            "grant_types", [GrantType.AUTHORIZATION_CODE.value, GrantType.REFRESH_TOKEN.value]
        )
        scope = metadata.get("scope", "")
        auth_method = metadata.get("token_endpoint_auth_method", ClientAuthMethod.NONE.value)
        name = metadata.get("client_name", "")
        if (
            not isinstance(redirect_uris, list)
            or not all(isinstance(one, str) for one in redirect_uris)
            or not isinstance(grant_types, list)
            or not isinstance(scope, str)
            or not isinstance(name, str)
        ):
            raise OAuthError(
                OAuthErrorCode.INVALID_CLIENT_METADATA,
                "redirect_uris and grant_types are lists of text; scope and client_name text",
            )
        served = {one.value for one in GrantType}
        unknown = [one for one in grant_types if one not in served]
        if unknown or auth_method not in {one.value for one in ClientAuthMethod}:
            raise OAuthError(
                OAuthErrorCode.INVALID_CLIENT_METADATA,
                f"grant_types are among {sorted(served)} and token_endpoint_auth_method among "
                f"{[one.value for one in ClientAuthMethod]}",
            )
        return self.register(
            redirect_uris,
            name,
            [GrantType(one) for one in grant_types],
            scope.split(),
            auth_method != ClientAuthMethod.NONE.value,
            initial_access_token,
        )

    def served_endpoints(self) -> set[AuthorizationServerEndpoint]:
        return super().served_endpoints() | {AuthorizationServerEndpoint.REGISTRATION}
