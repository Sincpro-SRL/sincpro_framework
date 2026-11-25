"""What a session of a `TokenIssuer` keeps beside its tokens: how long it lives, and what the
sign-in knew that never goes in a token (#336, #337).

    SessionLifetime(idle=timedelta(days=30))                       only while it is used
    SessionLifetime(idle=timedelta(days=14), at_most=timedelta(days=90))

    attributes.save("sid_9f…", {"credential": "…"}, ttl=timedelta(days=30))
    attributes.find("sid_9f…")      ->  {"credential": "…"}      every refresh and every call
    attributes.delete("sid_9f…")                                  the session revoked

Context: an access token is read by whoever holds it — the client, its logs. What a sign-in
learned that the service needs on every call but nobody else may read (the customer's credential
to its ERP, the plan it bought, the agents it enabled) stays on the server, keyed by the
session's digest, and comes back into the identity when the token is verified.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject


class SessionLifetime(DataTransferObject):
    """How long one session lives: `idle` after its last refresh, and never past `at_most` after
    it began — `None`, no absolute end: it lasts while it is used."""

    model_config = ConfigDict(frozen=True)

    idle: timedelta
    at_most: timedelta | None = None


class SessionAttributes(ABC):
    """The port: what a session keeps on the server, by its id — stored by digest."""

    @abstractmethod
    def save(self, session: str, values: Mapping[str, Any], ttl: timedelta) -> None:
        """Keep `values` for `session` for `ttl` — replacing what it kept, extending its life."""

    @abstractmethod
    def find(self, session: str) -> dict[str, Any] | None:
        """What `session` keeps — `None` when it keeps nothing: never saved, expired or deleted."""

    @abstractmethod
    def delete(self, session: str) -> None:
        """Forget what `session` keeps — the session revoked; nothing when it kept nothing."""
