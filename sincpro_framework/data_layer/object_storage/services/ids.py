"""`PublicationIds`: the id a publication is reached by — 128 random bits, and their HMAC-SHA256 when
there is a secret, so an id not made here is refused before any store is asked.

    ids = PublicationIds("the-service-secret")
    raw, id = ids.new()                     ->  "0b9c…", "0b9c….Zk3…"
    ids.unsigned(id)                        ->  "0b9c…"
    ids.unsigned("0b9c….forged")            ->  PublicationRefused(FORGED)

Context: the secret is the project's, the same on every replica. Another signature — another
algorithm, a key rotation — is an override of `signed` and `unsigned`.
"""

import base64
import hashlib
import hmac
import uuid

from sincpro_framework.data_layer.object_storage.domain import exceptions
from sincpro_framework.data_layer.object_storage.domain.publication import Refusal


class PublicationIds:
    """Makes and verifies publication ids with one secret, or none."""

    def __init__(self, secret: str | bytes | None = None) -> None:
        """`secret` signs every id; without one an id is its random bits alone."""
        self.secret = secret.encode() if isinstance(secret, str) else secret

    def signed(self, raw: str) -> str:
        """The id its holder is handed for `raw`: `raw`, and its HMAC-SHA256 after a dot when
        there is a secret."""
        if self.secret is None:
            return raw
        digest = hmac.new(self.secret, raw.encode(), hashlib.sha256).digest()
        return f"{raw}.{base64.urlsafe_b64encode(digest).decode().rstrip('=')}"

    def unsigned(self, id: str) -> str:
        """The `raw` an id was made from, when it was made here — `PublicationRefused`
        (`FORGED`) otherwise."""
        if self.secret is None:
            return id
        raw = id.partition(".")[0]
        if not hmac.compare_digest(self.signed(raw), id):
            raise exceptions.PublicationRefused(
                Refusal.FORGED, "the publication was not made by this service"
            )
        return raw

    def new(self) -> tuple[str, str]:
        """A new `raw`, and the id its holder is handed."""
        raw = uuid.uuid4().hex
        return raw, self.signed(raw)
