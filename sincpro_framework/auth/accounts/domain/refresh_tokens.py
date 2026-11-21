"""The refresh tokens this service issued: what is kept of each — never the token, its digest —
and the store that rotates them atomically. Every token rotated from a session's first shares
its `family`."""

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import ConfigDict

from sincpro_framework.auth.domain.identity import Assurance
from sincpro_framework.sincpro_abstractions import DataTransferObject


class RefreshRecord(DataTransferObject):
    """A refresh token this service issued, kept by its digest — never the token. Its `family`
    is the session: every token rotated from the first one shares it."""

    model_config = ConfigDict(frozen=True)

    digest: str
    family: str
    subject: str
    kind: str = "user"
    tenant: str | None = None
    client: str | None = None
    roles: frozenset[str] = frozenset()
    """The roles the identity carried at sign-in — carried again by every refreshed token."""
    scopes: frozenset[str] = frozenset()
    assurance: Assurance = Assurance()
    authenticated_at: datetime
    issued_at: datetime
    expires_at: datetime
    """When this token stops working unused — `idle_for` after it was issued."""
    session_ends: datetime
    """When the session ends whatever is done — `at_most` after it started."""
    rotated_at: datetime | None = None


class RefreshTokens(ABC):
    """The port: refresh tokens by digest, rotated atomically."""

    @abstractmethod
    def save(self, record: RefreshRecord) -> None:
        """Keep `record` — a session's first refresh token."""

    @abstractmethod
    def find(self, digest: str) -> RefreshRecord | None:
        """The record under `digest`, rotated or not; `None` when there is none."""

    @abstractmethod
    def rotate(self, digest: str, successor: RefreshRecord, at: datetime) -> bool:
        """Mark `digest` rotated at `at` and keep `successor` — only when it was not rotated yet,
        atomically: of two refreshes racing with one token, exactly one wins."""

    @abstractmethod
    def sessions_of(self, subject: str) -> list[RefreshRecord]:
        """The live refresh token of each session `subject` holds — a "signed in on" screen."""
