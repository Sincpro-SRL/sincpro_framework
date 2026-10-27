"""`Publications`: an object made reachable by an id — published, checked, redeemed, revoked — on the
`KeyValueStore` the context stands on, with nothing else to set.

    publications = Publications(PublicationIds(secret))
    published = publications.publish("inbox/scan.pdf", Access.WRITE, timedelta(minutes=5), 1, {},
                                     Grant(access=Access.READ, expires_in=timedelta(hours=1), max_uses=1))
    publications.opened(published.id, Access.WRITE, spend=True)    the upload, once
    publications.opened(published.id, Access.READ, spend=False)    a preview: the same id, nothing spent
    publications.opened(published.id, Access.READ, spend=True)     the claim, once
    publications.revoke(published.id)

Context: where a publication is saved is read at every call — the store given, else the bus in
play's, else the process's (`store_context_in(...)`), else this process's memory — so every replica
that shares the context's store shares its publications. It is written once, lasting its own time
and a day more (to answer `EXPIRED` or `USED_UP`, never `UNKNOWN`, to a late caller); its uses are
a counter (`increment`), so a use is spent in one atomic step, with no read to compare and nothing
retried — of two redeems of the last use, one opens it. Its `then` opens when the first access's
uses are spent, for its own time from that moment.
"""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sincpro_framework.context import ContextStore, use_context
from sincpro_framework.context.entrypoint.resolution import key_value_store_of
from sincpro_framework.data_layer.object_storage.domain import exceptions
from sincpro_framework.data_layer.object_storage.domain.object_store import Access
from sincpro_framework.data_layer.object_storage.domain.publication import (
    Grant,
    Publication,
    Refusal,
)
from sincpro_framework.data_layer.object_storage.services.ids import PublicationIds
from sincpro_framework.data_layer.store import KeyValueStore

RECORDS = "sincpro:object_publication"
"""The section of the `KeyValueStore` every publication is saved in."""

GRACE = timedelta(days=1)
"""How long a publication is kept past its time — to say why it no longer opens."""


class Publications:
    """Publications of objects, by id."""

    def __init__(
        self,
        ids: PublicationIds,
        store: ContextStore | None = None,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`ids` makes and verifies the ids; `store` is the context store whose `KeyValueStore`
        publications are saved on, else the context's store in play; `now` the clock they
        expire by."""
        self.ids = ids
        self.store = store
        self.now = now

    def saved_in(self) -> KeyValueStore:
        """Where publications are saved: the `KeyValueStore` of the store given, else of the
        context's store in play."""
        return key_value_store_of(use_context().store_in_use(self.store))

    def publish(
        self,
        key: str,
        access: Access,
        expires_in: timedelta,
        max_uses: int | None,
        metadata: Mapping[str, Any],
        then: Grant | None = None,
    ) -> Publication:
        """Make the object under `key` reachable by a new id, to `access` it, for `expires_in`,
        `max_uses` times — every time with `None` — and, once those are spent, to `then`.
        Nothing is asked of the object: one published to write is not there yet. A `then` on
        uses without end would never open: `ProgrammingError`."""
        if then is not None and max_uses is None:
            raise exceptions.ProgrammingError(
                "a publication opens to `then` once its uses are spent: give it max_uses"
            )
        raw, id = self.ids.new()
        published = Publication(
            id=id,
            key=key,
            access=access,
            expires_at=self.now() + expires_in,
            max_uses=max_uses,
            metadata=dict(metadata),
            then=then,
        )
        life = expires_in + (then.expires_in if then is not None else timedelta()) + GRACE
        record = published.model_dump_json(exclude={"id", "uses"}).encode()
        self.saved_in().set(f"{RECORDS}:{raw}", record, life)
        return published

    def _current(self, raw: str, id: str, saved: KeyValueStore) -> tuple[Publication, int]:
        """The publication `raw` names as it stands now, and which of its accesses — `0` the
        first, `1` its `then` once the first is spent. `PublicationRefused` (`UNKNOWN`) when no
        publication has it."""
        held = saved.get(f"{RECORDS}:{raw}")
        if held is None:
            raise exceptions.PublicationRefused(
                Refusal.UNKNOWN, "no publication has that id: ask for a new one"
            )
        uses = int(saved.get(f"{RECORDS}:{raw}:uses:0") or 0)
        first = Publication.model_validate({**json.loads(held), "id": id, "uses": uses})
        if first.then is None or first.max_uses is None or first.uses < first.max_uses:
            return first, 0
        opened_at = saved.get(f"{RECORDS}:{raw}:opened")
        since = datetime.fromisoformat(opened_at.decode()) if opened_at else self.now()
        second = first.model_copy(
            update={
                "access": first.then.access,
                "expires_at": since + first.then.expires_in,
                "max_uses": first.then.max_uses,
                "uses": int(saved.get(f"{RECORDS}:{raw}:uses:1") or 0),
                "then": None,
            }
        )
        return second, 1

    def opened(self, id: str, access: Access, spend: bool) -> Publication:
        """The publication `id` names, when it opens for `access` — one use spent with `spend`.
        `PublicationRefused`, with its `reason` and the publication when it is known, when it
        does not.

        1. The id was made here (`FORGED` otherwise) and a publication has it (`UNKNOWN`).
        2. It stands at the access asked — asking the first once it is spent is `USED_UP`,
           any other access `WRONG_ACCESS` — before its time (`EXPIRED`), with uses left
           (`USED_UP`).
        3. With `spend`, one use is counted atomically; a count past `max_uses` lost the race
           (`USED_UP`). The last use of the first access opens `then` from now.
        Final: the publication as it stands after this call.
        """
        raw = self.ids.unsigned(id)
        saved = self.saved_in()
        published, step = self._current(raw, id, saved)
        if published.access is not access:
            spent_first = step == 1
            raise exceptions.PublicationRefused(
                Refusal.USED_UP if spent_first else Refusal.WRONG_ACCESS,
                (
                    f"the publication was already used to {access.value}"
                    if spent_first
                    else f"the publication is not to {access.value}"
                ),
                published,
            )
        if self.now() >= published.expires_at:
            raise exceptions.PublicationRefused(
                Refusal.EXPIRED, "the publication expired: ask for a new one", published
            )
        if published.max_uses is not None and published.uses >= published.max_uses:
            raise exceptions.PublicationRefused(
                Refusal.USED_UP,
                "the publication was used as many times as it allowed",
                published,
            )
        if not spend:
            return published
        lasts = published.expires_at - self.now() + GRACE
        if published.then is not None:
            lasts += published.then.expires_in
        uses = saved.increment(f"{RECORDS}:{raw}:uses:{step}", lasts)
        spent = published.model_copy(update={"uses": uses})
        if published.max_uses is not None and uses > published.max_uses:
            raise exceptions.PublicationRefused(
                Refusal.USED_UP, "the publication was used as many times as it allowed", spent
            )
        if published.then is not None and uses == published.max_uses:
            saved.add(
                f"{RECORDS}:{raw}:opened",
                self.now().isoformat().encode(),
                published.then.expires_in + GRACE,
            )
        return spent

    def revoke(self, id: str) -> None:
        """Close the publication `id` names before its time; one already gone is not an error."""
        self.saved_in().delete(f"{RECORDS}:{self.ids.unsigned(id)}")
