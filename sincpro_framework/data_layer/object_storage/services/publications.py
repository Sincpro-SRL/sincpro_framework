"""`Publications`: an object made reachable by an id — published, checked, redeemed, revoked — saved as
an entry of the store the context saves in.

    publications = Publications(PublicationIds(secret))
    published = publications.publish("exports/invoices.csv", Access.READ, timedelta(hours=1), 1, {})
    publications.check(published.id, Access.READ)      ->  Publication, nothing spent
    publications.redeem(published.id, Access.READ)     ->  Publication, one use spent
    publications.revoke(published.id)

Context: where a publication is saved is read at every call — the store given, else the bus in
play's, else the process's (`store_context_in(...)`), else this process's memory — so every replica
that shares the context's store shares its publications, with nothing else to set. A use is spent
comparing the entry's version (`save_entry(..., expected)`): of two redeems of one use left, one
opens it. A publication lives as long as that store keeps an entry (`entry_ttl`, 7 days by default).
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sincpro_framework.context import ContextStore, use_context
from sincpro_framework.data_layer.object_storage.domain import exceptions
from sincpro_framework.data_layer.object_storage.domain.object_store import Access
from sincpro_framework.data_layer.object_storage.domain.publication import (
    Publication,
    Refusal,
)
from sincpro_framework.data_layer.object_storage.services.ids import PublicationIds

FOLDER = "SINCPRO_OBJECT_PUBLICATION"
"""The folder of the context's store every publication is an entry of."""


class Publications:
    """Publications of objects, by id."""

    def __init__(
        self,
        ids: PublicationIds,
        store: ContextStore | None = None,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`ids` makes and verifies the ids; `store` is where publications are saved, else the
        context's store; `now` the clock they expire by."""
        self.ids = ids
        self.store = store
        self.now = now

    def saved_in(self) -> ContextStore:
        """Where publications are saved: the store given, else the context's store in play."""
        return use_context().store_in_use(self.store)

    def publish(
        self,
        key: str,
        access: Access,
        expires_in: timedelta,
        max_uses: int | None,
        metadata: Mapping[str, Any],
    ) -> Publication:
        """Make the object under `key` reachable by a new id, to `access` it, for `expires_in`,
        `max_uses` times — every time with `None`. Nothing is asked of the object: one
        published to write is not there yet."""
        raw, id = self.ids.new()
        published = Publication(
            id=id,
            key=key,
            access=access,
            expires_at=self.now() + expires_in,
            max_uses=max_uses,
            metadata=dict(metadata),
        )
        values = published.model_dump(mode="json", exclude={"id"})
        self.saved_in().save_entry(f"{FOLDER}:{raw}", values, 0)
        return published

    def opened(self, id: str, access: Access, spend: bool) -> Publication:
        """The publication `id` names, when it opens for `access` — one use spent with `spend`,
        compared against the version read so two redeems never spend the same use.
        `PublicationRefused`, with its `reason`, when it does not open."""
        raw = self.ids.unsigned(id)
        store = self.saved_in()
        while True:
            entry = store.get_entry(f"{FOLDER}:{raw}")
            if entry is None:
                raise exceptions.PublicationRefused(
                    Refusal.UNKNOWN, "no publication has that id: ask for a new one"
                )
            published = Publication.model_validate({**entry.values, "id": id})
            if published.access is not access:
                raise exceptions.PublicationRefused(
                    Refusal.WRONG_ACCESS, f"the publication is not to {access.value}"
                )
            if self.now() >= published.expires_at:
                raise exceptions.PublicationRefused(
                    Refusal.EXPIRED, "the publication expired: ask for a new one"
                )
            if published.max_uses is not None and published.uses >= published.max_uses:
                raise exceptions.PublicationRefused(
                    Refusal.USED_UP, "the publication was used as many times as it allowed"
                )
            if not spend:
                return published
            spent = published.model_copy(update={"uses": published.uses + 1})
            try:
                store.save_entry(
                    entry.key, spent.model_dump(mode="json", exclude={"id"}), entry.version
                )
            except exceptions.EntryConflict:
                continue
            return spent

    def revoke(self, id: str) -> None:
        """Close the publication `id` names before its time; one already gone is not an error."""
        self.saved_in().delete_entry(f"{FOLDER}:{self.ids.unsigned(id)}")
