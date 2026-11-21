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
from datetime import datetime
from typing import Any, Protocol

from pydantic import ConfigDict, Field

from sincpro_framework.auth.domain.http import safe_url
from sincpro_framework.auth.domain.identity import Identity
from sincpro_framework.sincpro_abstractions import DataTransferObject


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

    def __repr__(self) -> str:
        return f"OutgoingRequest({self.method} {safe_url(self.url)}, tenant={self.tenant!r})"

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
    """Seals what a shared store holds of a secret — a token cached on Redis — and opens it."""

    def seal(self, plain: bytes) -> bytes:
        """`plain` encrypted and authenticated — nothing about it readable without the key."""
        ...

    def open(self, sealed: bytes) -> bytes:
        """What `seal` sealed — `ValueError` for what another key sealed, or was changed."""
        ...


class HeldGrant(DataTransferObject):
    """A grant this service holds for an API that issues refresh tokens — the refresh token, the
    access token it last got, when that one expires. Its `version` counts the refreshes."""

    model_config = ConfigDict(frozen=True)

    refresh_token: str = Field(repr=False)
    access_token: str | None = Field(default=None, repr=False)
    expires_at: datetime | None = None
    version: int = 0


class HeldGrants(ABC):
    """The port: where held grants live, written by compare-and-set — two replicas refreshing
    at once never both store their answer."""

    @abstractmethod
    def current(self, key: str) -> HeldGrant | None:
        """The grant held under `key`, if any."""

    @abstractmethod
    def replace(self, key: str, expected: HeldGrant | None, new: HeldGrant | None) -> bool:
        """Write `new` (`None` drops it) only when `key` holds `expected` now — `True` when it
        was written."""
