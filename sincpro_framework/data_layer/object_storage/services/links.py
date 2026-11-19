"""`ObjectLinks`: a link to an object that saves nothing — its key and its end, signed with the
storage's secret — for what is shown many at a time: the picture on each row of a list.

    links = ObjectLinks(b"the-service-secret", now)
    token = links.link("photos/01a1….jpg", timedelta(hours=1))    ->  "cGhvdG9z….1763600400.Zk3…"
    links.linked(token)                                          ->  Linked(key="photos/01a1….jpg", ends_at=…)
    links.linked("cGhvdG9z….1763600400.forged")                  ->  PublicationRefused(FORGED)
    links.linked(token)  after its end                           ->  PublicationRefused(EXPIRED)

Context: a publication (`Publications`) is saved — it counts its uses and can be revoked — so one
per row would write fifty entries to show fifty photos. A link is checked by its signature alone:
it is never saved, so it can be neither counted nor revoked; it ends. Its end is rounded up to the
next multiple of `expires_in`, so an object keeps the same link — and a browser's cache of it —
for that window, and lives between one and two `expires_in`.
"""

import base64
import binascii
import hashlib
import hmac
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from sincpro_framework.data_layer.object_storage.domain import exceptions
from sincpro_framework.data_layer.object_storage.domain.publication import Refusal
from sincpro_framework.exceptions import ProgrammingError


class Linked(NamedTuple):
    """What a link opens: the object's key, and when the link ends."""

    key: str
    ends_at: datetime


def _encoded(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _decoded(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class ObjectLinks:
    """Makes and opens links with one secret — none, no link."""

    def __init__(self, secret: bytes | None, now: Callable[[], datetime]) -> None:
        """`secret` signs every link; `now` is the clock they end by."""
        self.secret = secret
        self.now = now

    def _signature(self, body: str) -> str:
        """HMAC-SHA256 of `body`, cut to 128 bits — as a publication's id is signed."""
        if self.secret is None:
            raise ProgrammingError(
                "a link needs the storage's secret: ObjectStorage(store, secret)"
            )
        return _encoded(hmac.new(self.secret, body.encode(), hashlib.sha256).digest()[:16])

    def link(self, key: str, expires_in: timedelta) -> str:
        """`key` and its end, signed: `{key}.{end}.{signature}`, the key in base64url, the end in
        seconds since the epoch rounded up to a multiple of `expires_in`. `ProgrammingError`
        without a secret: an unsigned link would open any key to whoever writes one."""
        window = expires_in.total_seconds()
        if window <= 0:
            raise ProgrammingError(f"a link lasts some time, not {expires_in}")
        end = math.ceil((self.now() + expires_in).timestamp() / window) * window
        body = f"{_encoded(key.encode())}.{int(end)}"
        return f"{body}.{self._signature(body)}"

    def linked(self, token: str) -> Linked:
        """The key `token` links to and its end, when this service signed it and it has not
        ended — `PublicationRefused` with `FORGED` or `EXPIRED` otherwise.

        1. Three parts, the signature of the first two — else forged, before reading them.
        2. Its end, against the clock.
        Final: the key it names.
        """
        body, _, signature = token.rpartition(".")
        if not body or not hmac.compare_digest(self._signature(body), signature):
            raise exceptions.PublicationRefused(
                Refusal.FORGED, "the link was not made by this service"
            )
        encoded, _, end = body.partition(".")
        try:
            key = _decoded(encoded).decode()
            ends_at = datetime.fromtimestamp(int(end), UTC)
        except (binascii.Error, UnicodeDecodeError, ValueError) as broken:
            raise exceptions.PublicationRefused(
                Refusal.FORGED, "the link was not made by this service"
            ) from broken
        if self.now() >= ends_at:
            raise exceptions.PublicationRefused(Refusal.EXPIRED, "the link has ended")
        return Linked(key, ends_at)
