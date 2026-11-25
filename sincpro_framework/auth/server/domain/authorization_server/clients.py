"""The clients an authorization server knows — where its answer may go, which grants and scopes
a client may use, and whether it proves itself with a secret — and the registry that holds them.

    OAuthClient(client_id="cl_9f…", redirect_uris=("https://claude.ai/api/mcp/auth_callback",))
        .redirects_to("https://claude.ai/api/mcp/auth_callback")    ->  True
        .redirects_to("https://claude.ai/api/mcp/auth_callback/")   ->  False: exact, OAuth 2.1

Context: a public client — a browser app, a CLI, an MCP host — holds no secret and proves the
request is its own by PKCE alone; a confidential one proves itself with a secret, kept here by its
digest only.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from enum import StrEnum

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject


class GrantType(StrEnum):
    """How a client obtains tokens — the grants this authorization server knows."""

    AUTHORIZATION_CODE = "authorization_code"
    REFRESH_TOKEN = "refresh_token"


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

    @property
    def is_public(self) -> bool:
        """Whether it holds no secret: PKCE alone proves its requests."""
        return self.secret_digest is None

    def redirects_to(self, uri: str) -> bool:
        """Whether `uri` is one it registered — compared exactly, never by prefix."""
        return uri in self.redirect_uris

    def uses(self, grant: GrantType) -> bool:
        """Whether it was registered for `grant`."""
        return grant in self.grant_types

    def may_ask_for(self, scopes: Iterable[str]) -> bool:
        """Whether every one of `scopes` is one it may ask for."""
        return not self.scopes or frozenset(scopes) <= self.scopes


class ClientRegistry(ABC):
    """The port: the clients by their id."""

    @abstractmethod
    def find(self, client_id: str) -> OAuthClient | None:
        """The client registered under `client_id` — `None` when there is none."""

    @abstractmethod
    def save(self, client: OAuthClient) -> None:
        """Register `client`, or replace the one under its id."""
