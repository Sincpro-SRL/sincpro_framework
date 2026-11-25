"""`MetadataDocumentClients` and `ClientRegistries`: a client identified by the URL of its own
metadata document — OAuth Client ID Metadata Documents, what the MCP authorization spec
recommends over registration (#338) — beside the clients that registered.

    clients = ClientRegistries(KeyValueOAuthClients(), MetadataDocumentClients())
    server = AuthorizationServer(issuer, token_issuer=tokens, client_registry=clients)
    clients.find("https://claude.ai/oauth/mcp-client.json")
        ->  OAuthClient(client_id="https://claude.ai/oauth/mcp-client.json", redirect_uris=(...),
                        name="Claude")          fetched once, cached; no registration step

Context: the client publishes a JSON document at an https URL and uses that URL as its
`client_id`; the server fetches it at `/authorize` and `/token`. Fetching a URL a stranger chose
is a server-side request, so it is guarded: `https` only, a path, no user information nor
fragment, a small body, a short timeout, and `is_fetchable(url)` — literal IP addresses and
`localhost` refused by default; override to allow-list the hosts you trust. The document must
name the URL as its `client_id`, register redirects a client may have, and be a public client:
PKCE proves its requests.
"""

import ipaddress
from datetime import timedelta
from urllib.parse import urlsplit

from sincpro_framework.auth.common.adapters.key_value import KeyValueStoreMixin
from sincpro_framework.auth.common.adapters.urllib_http import UrllibHttp
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.http import HttpClient, HttpRequest
from sincpro_framework.auth.common.infrastructure.digests import digest
from sincpro_framework.auth.server.domain.authorization_server.clients import (
    ClientAuthMethod,
    ClientRegistry,
    GrantType,
    OAuthClient,
    is_registrable_redirect_uri,
)
from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.sincpro_logger import logger

MAX_DOCUMENT_BYTES = 10_240
"""How large a metadata document may be — a few redirects and a name fit in far less."""


class MetadataDocumentClients(KeyValueStoreMixin, ClientRegistry):
    """Clients whose `client_id` is the https URL of their metadata document — fetched, checked
    and cached on a `KeyValueStore` (the running context's when none is given)."""

    supports_metadata_documents = True

    def __init__(
        self,
        http: HttpClient | None = None,
        store: KeyValueStore | None = None,
        cache_for: timedelta = timedelta(hours=1),
        timeout: timedelta = timedelta(seconds=5),
        prefix: str = "sincpro:auth:oauth:cimd",
    ) -> None:
        """`http` fetches the documents — `UrllibHttp` capped at `MAX_DOCUMENT_BYTES` by
        default; `cache_for` how long a document is trusted before it is fetched again."""
        self._init_key_value_store(store, prefix)
        self.http = http if http is not None else UrllibHttp(max_bytes=MAX_DOCUMENT_BYTES)
        self.cache_for = cache_for
        self.timeout = timeout

    def is_metadata_document_id(self, client_id: str) -> bool:
        """Whether `client_id` is the URL of a metadata document — https, a host, a path, no
        user information nor fragment."""
        parts = urlsplit(client_id)
        return (
            parts.scheme == "https"
            and bool(parts.hostname)
            and parts.path not in ("", "/")
            and parts.username is None
            and "#" not in client_id
        )

    def is_fetchable(self, url: str) -> bool:
        """Whether this server may fetch `url` — not a literal IP address nor `localhost`.
        Override to allow only the hosts you trust (`claude.ai`, `chatgpt.com`)."""
        host = urlsplit(url).hostname or ""
        if host == "localhost" or host.endswith(".localhost"):
            return False
        try:
            ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            return True
        return False

    def client_from_document(self, url: str, document: dict) -> OAuthClient | None:
        """The client a document describes — `None` when it does not name `url` as its
        `client_id`, registers no redirect or one a client may not have, or is not a public
        client. Override to accept more of the document."""
        redirect_uris = document.get("redirect_uris")
        method = document.get("token_endpoint_auth_method", ClientAuthMethod.NONE.value)
        if (
            document.get("client_id") != url
            or not isinstance(redirect_uris, list)
            or not redirect_uris
            or not all(
                isinstance(one, str) and is_registrable_redirect_uri(one)
                for one in redirect_uris
            )
            or method != ClientAuthMethod.NONE.value
        ):
            return None
        known = {one.value for one in GrantType}
        asked = document.get("grant_types") or [one.value for one in GrantType]
        name = document.get("client_name")
        return OAuthClient(
            client_id=url,
            redirect_uris=tuple(redirect_uris),
            name=name if isinstance(name, str) else "",
            grant_types=frozenset(
                GrantType(one) for one in asked if isinstance(one, str) and one in known
            ),
            token_endpoint_auth_method=ClientAuthMethod.NONE,
        )

    def _fetch(self, url: str) -> OAuthClient | None:
        """The document at `url`, read — `None` for an answer that is not a 2xx JSON object
        describing an acceptable client;
        an endpoint that does not answer is `EndpointUnavailable` (503): the client did not fail.
        """
        answer = self.http.send(
            HttpRequest(url=url, headers={"accept": "application/json"}, timeout=self.timeout)
        )
        if not answer.ok:
            logger.warning("a client metadata document was not served", status=answer.status)
            return None
        return self.client_from_document(url, answer.json_object())

    def find(self, client_id: str) -> OAuthClient | None:
        """The client `client_id` names — from the cache, else fetched; `None` when it is not a
        metadata document URL this server may fetch, or its document is not acceptable."""
        if not self.is_metadata_document_id(client_id) or not self.is_fetchable(client_id):
            return None
        key = f"{self.prefix}:{digest(client_id)}"
        cached = self.store().get(key)
        if cached is not None:
            return OAuthClient.model_validate_json(cached)
        client = self._fetch(client_id)
        if client is not None:
            self.store().set(key, client.model_dump_json().encode(), self.cache_for)
        return client

    def save(self, client: OAuthClient) -> None:
        """Never: such a client is not registered — its document says who it is."""
        raise exceptions.ProgrammingError(
            "a metadata document client is fetched, never registered: register through "
            "another registry (ClientRegistries puts registrations in the first)"
        )

    def forget(self, client_id: str) -> None:
        """Drop the cached document of `client_id` — fetched again on its next request."""
        self.store().delete(f"{self.prefix}:{digest(client_id)}")


class ClientRegistries(ClientRegistry):
    """Several registries as one: the first that knows a client answers; a registration is saved
    in the first."""

    def __init__(self, *registries: ClientRegistry) -> None:
        if not registries:
            raise exceptions.ProgrammingError("ClientRegistries needs at least one registry")
        self.registries = registries
        self.supports_metadata_documents = any(
            one.supports_metadata_documents for one in registries
        )

    def find(self, client_id: str) -> OAuthClient | None:
        for registry in self.registries:
            found = registry.find(client_id)
            if found is not None:
                return found
        return None

    def save(self, client: OAuthClient) -> None:
        self.registries[0].save(client)
