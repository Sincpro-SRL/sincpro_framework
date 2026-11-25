"""What this service presents when it calls another — the client half of auth.

    bank = ClientCredentials(client_id="billing", client_secret=settings.bank_secret,
                             issuer="https://auth.bank.bo", scope=["payments"])
    bank.headers_for(OutgoingRequest(url="https://api.bank.bo/qr", method="POST"))
        ->  {"authorization": "Bearer eyJ…"}

Context: a `TokenSource` belongs to the adapter that calls the API — injected with
`add_dependency` in `infrastructure/dependencies.py`, never handed to a use case — and is set
for one API: the audience is the source's, never read from the URL, so a token for one API is
never sent to another. What it fetches it caches; asked again after a 401 (`invalidate`), it
answers whether new headers could help — the caller retries once, never in a loop.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol

from pydantic import ConfigDict, Field

from sincpro_framework.auth.client.domain.token_response import (
    TokenResponse,
    token_renewal_time,
)
from sincpro_framework.auth.common.domain.http import redact_url
from sincpro_framework.auth.common.domain.identity import Identity
from sincpro_framework.sincpro_abstractions import DataTransferObject


def parse_bearer_token(
    headers: Mapping[str, str], header: str = "authorization"
) -> str | None:
    """The token of `<scheme> <token>` in `header` — `Bearer` or `DPoP` — if any.

    parse_bearer_token({"Authorization": "Bearer at-1"})  ->  "at-1"
    """
    value = {name.lower(): one for name, one in dict(headers).items()}.get(header, "")
    _, _, token = value.partition(" ")
    return token.strip() or None


class OutgoingRequest(DataTransferObject):
    """The request credentials are asked for — what a source may sign or bind a token to."""

    model_config = ConfigDict(frozen=True)

    url: str
    method: str = "GET"
    body: bytes | None = Field(default=None, repr=False)
    """Read only by a source that `needs_body` — a signature over it."""
    tenant: str | None = None
    """Whose credentials, for a source per tenant — the identity's tenant when not given."""
    identity: Identity | None = None
    """Who the call acts for — the identity in play when not given."""

    def bind_caller(self, identity: Identity) -> "OutgoingRequest":
        """This request acting for its own identity, else `identity`; for its own tenant, else
        that identity's."""
        acting = self.identity or identity
        return self.model_copy(
            update={"identity": acting, "tenant": self.tenant or acting.tenant}
        )

    def __repr__(self) -> str:
        return (
            f"OutgoingRequest({self.method} {redact_url(self.url)}, tenant={self.tenant!r})"
        )

    __str__ = __repr__


class TokenSource(ABC):
    """The port: the headers an outgoing request carries to authenticate."""

    name: str = "token-source"
    needs_body: bool = False
    """Whether the request's body is read — a signature over it."""

    @abstractmethod
    def headers_for(self, request: OutgoingRequest) -> dict[str, str]:
        """The headers `request` carries — fetching and caching a token when the source does."""

    def invalidate(
        self,
        request: OutgoingRequest,
        presented: Mapping[str, str],
        answer: Mapping[str, str] | None = None,
    ) -> bool:
        """The headers `presented` were refused (401); `answer` is the response's headers.
        `True` when asking `headers_for` again may help — a token dropped, a nonce learnt.
        A static credential answers `False`: sending it again fails the same way."""
        return False

    def describe(self) -> dict[str, Any]:
        """What the source is, for a log or a screen — never a secret."""
        return {"source": type(self).__name__, "name": self.name}


class Sealer(Protocol):
    """Encrypts what a shared store holds of a secret — a token cached on Redis — and decrypts
    it."""

    def encrypt(self, plain: bytes) -> bytes:
        """`plain` encrypted and authenticated — nothing about it readable without the key."""
        ...

    def decrypt(self, sealed: bytes) -> bytes:
        """What `encrypt` encrypted — `ValueError` for what another key encrypted, or was
        changed."""
        ...


class OAuthGrant(DataTransferObject):
    """A grant this service holds from another authorization server — the refresh token, the
    access token it last got, when that one arrived and expires. `version` counts the writes of
    a refresh token: a compare-and-set is judged on it, never on the access token's expiry."""

    model_config = ConfigDict(frozen=True)

    refresh_token: str = Field(repr=False)
    access_token: str | None = Field(default=None, repr=False)
    token_type: str = "Bearer"
    received_at: datetime | None = None
    expires_at: datetime | None = None
    version: int = 0

    @classmethod
    def build(cls, issued: TokenResponse, refresh_token: str, version: int) -> "OAuthGrant":
        """The grant `issued` holds, with `refresh_token` — the rotated one, or the one spent
        when the server does not rotate."""
        return cls(
            refresh_token=refresh_token,
            access_token=issued.access_token.get_secret_value(),
            token_type=issued.token_type,
            received_at=issued.received_at,
            expires_at=issued.expires_at,
            version=version,
        )

    def is_valid(self, now: datetime, skew: timedelta) -> bool:
        """Whether its access token is still used at `now` — `token_renewal_time` not reached."""
        return (
            self.access_token is not None
            and self.expires_at is not None
            and now < token_renewal_time(self.received_at, self.expires_at, skew)
        )


class OAuthGrantStore(ABC):
    """The port: where the OAuth grants this service holds live, written by compare-and-set on
    their `version` — two replicas refreshing at once never both store their answer."""

    @abstractmethod
    def get(self, key: str) -> OAuthGrant | None:
        """The grant stored under `key`, if any."""

    @abstractmethod
    def compare_and_set(
        self, key: str, expected_version: int | None, grant: OAuthGrant | None
    ) -> bool:
        """Write `grant` (`None` deletes it) only when the grant stored under `key` has
        `expected_version` — `None`: there is none — `True` when it was written."""
