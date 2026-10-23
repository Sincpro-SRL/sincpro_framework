"""`Drafts`: the drafts of whoever the context says, kept in the context's store.

    use_context().drafts()                          this user's — within this tenant when there is one
    use_context().drafts(by=["session_id"])         this session's
    use_context().drafts(by=["user_id", "pos_id"])  this user's on this point of sale
    use_context().drafts(by=[])                     nobody's: shared by everybody of the tenant

A draft is an entry of the context's store, under one namespace and one owner:

    SINCPRO_ENTITY_DRAFT_CONTEXT : tenant_id=acme,tenant_ids=,user_id=ana : billing.Invoice%3A<uuid>
    └─ namespace                   └─ owner (`owner_of`)                   └─ the subject, encoded

The desktop, the browser and the phone of one user, in one tenant, read and write the same draft of
one record: it belongs to no execution and no session — unless a project says so with `by`. It lasts
the store's `kept_ttl` from its last keep, and outlives every browser that closed.

Context: whose a draft is comes from the context of the call, never from the subject the caller
writes, so one user's draft is never read by another for a subject spelled the same. The tenant
keys always take part, so a draft never crosses tenants; a context that says no tenant and no user —
a service keyed by its own ids — keeps its drafts all the same.
"""

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote, unquote

from sincpro_framework.context.domain import exceptions
from sincpro_framework.context.domain.drafts import Draft
from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.context.domain.store import ContextStore, Entry
from sincpro_framework.context.entrypoint.owner import owner_of

BY_USER = (ContextKey.USER_ID,)
"""Whose a draft is unless told otherwise, beside the tenant: the user's."""


class Drafts:
    """The drafts of one owner — the tenant keys, and the values of `by`, read from a context."""

    def __init__(
        self, store: ContextStore, context: Mapping[Any, Any], by: Sequence[str] = BY_USER
    ) -> None:
        self.store = store
        self.owner = owner_of(context, by)
        """Whose these drafts are: `tenant_id=acme,tenant_ids=,user_id=ana`."""
        self.folder = f"{ContextKey.ENTITY_DRAFT_CONTEXT}:{self.owner}"

    def _key(self, subject: str) -> str:
        return f"{self.folder}:{quote(subject, safe='')}"

    def _draft(self, subject: str, entry: Entry) -> Draft:
        return Draft(
            key=subject,
            values=entry.values["values"],
            version=entry.version,
            origin_version=entry.values["origin_version"],
            kept_at=entry.kept_at,
        )

    def keep(
        self, subject: str, values: Mapping[str, Any], origin_version: int, expected: int
    ) -> Draft:
        """`values` as the draft of `subject`, under a new version.

            in      "invoice:7", {"discount": Decimal("50")}, origin 3, expected 0
            out     Draft(version=…, origin_version=3, values={"discount": "50"})

        `expected` is the version the caller read, `0` for none; anything else raises
        `DraftConflict`. The TTL starts again. The draft answered is the one `read` answers
        until the next keep.
        """
        try:
            kept = self.store.keep_entry(
                self._key(subject),
                {"values": dict(values), "origin_version": origin_version},
                expected,
            )
        except exceptions.EntryConflict as conflict:
            raise exceptions.DraftConflict(
                subject, conflict.expected, conflict.current
            ) from None
        return self._draft(subject, kept)

    def read(self, subject: str) -> Draft | None:
        """The live draft of `subject`, or `None` when there is none, it was discarded or it
        expired."""
        found = self.store.entry(self._key(subject))
        return None if found is None else self._draft(subject, found)

    def discard(self, subject: str) -> None:
        """Drop the draft of `subject`; a tab still holding it cannot bring it back. One with
        none is not an error."""
        self.store.discard_entry(self._key(subject))

    def subjects(self) -> list[str]:
        """What this owner has a live draft of, the most recently kept first."""
        return [unquote(key.rsplit(":", 1)[1]) for key in self.store.entries(self.folder)]
