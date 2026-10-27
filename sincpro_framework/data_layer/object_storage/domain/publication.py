"""`Publication`: an object made reachable by an id — for a time, for a number of uses, to read or to
write — and why one is refused (`Refusal`).

    Publication(id="0b9c….Zk3", key="exports/invoices.csv", access=Access.READ,
                expires_at=…, max_uses=1, uses=0, metadata={"filename": "facturas.csv"})

Context: the id is all its holder has, and all an entrypoint puts in its URL, its message or its
answer — the publication knows no URL, no route, no host. Whoever receives the id back asks
`ObjectStorage.redeem` and serves or takes the bytes its own way.
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sincpro_framework.data_layer.object_storage.domain.object_store import Access
from sincpro_framework.sincpro_abstractions import DataTransferObject


class Refusal(StrEnum):
    """Why a publication's id does not open its object."""

    FORGED = "forged"
    """The id was not signed by this service — no store was asked."""
    UNKNOWN = "unknown"
    """No publication has that id: never published, revoked, or gone with its store's time."""
    EXPIRED = "expired"
    """Its time passed."""
    USED_UP = "used_up"
    """It was used as many times as it allowed."""
    WRONG_ACCESS = "wrong_access"
    """It was published to read and asked to write, or the other way."""


class Grant(DataTransferObject):
    """What a publication opens to once its first access is spent: another access, for its own
    time from that moment, its own number of uses.

        Grant(access=Access.READ, expires_in=timedelta(hours=1), max_uses=1)    upload, then claim once
    """

    access: Access
    expires_in: timedelta
    max_uses: int | None = None


class Publication(DataTransferObject):
    """An object reachable by `id` until `expires_at`, `max_uses` times — every time with `None`."""

    id: str
    """What its holder hands back — signed when the storage has a secret."""
    key: str
    access: Access
    expires_at: datetime
    max_uses: int | None = None
    uses: int = 0
    """How many times it was redeemed."""
    metadata: dict[str, Any] = {}
    """What the publisher said of it — a filename, who may use it, what to do after — read back
    by whoever checks or redeems it, to judge by rules of its own."""
    then: Grant | None = None
    """What it opens to once this access is spent — `None` once it has, or when it opens to
    nothing more."""
