"""`Drafts`: the drafts of whoever the context says, saved in the context's store.

    use_context().drafts()                          this user's — within this tenant when there is one
    use_context().drafts(by=["session_id"])         this session's
    use_context().drafts(by=[*Owner.BY_TENANT, "pos_id"])   this point of sale's, in this tenant
    use_context().drafts(by=Owner.BY_TENANT)                shared by everybody of the tenant
    use_context().drafts(by=())                     nobody's: one draft of a record, for everyone

A draft is an entry of the context's store, under one namespace and one owner:

    SINCPRO_ENTITY_DRAFT_CONTEXT : tenant_id=acme,tenant_ids=,user_id=ana : billing.Invoice%3A<uuid>
    └─ namespace                   └─ owner (`Owner.of`)                   └─ the subject, encoded

The desktop, the browser and the phone of one user, in one tenant, read and write the same draft of
one record: it belongs to no execution and no session — unless a project says so with `by`. It lasts
the store's `entry_ttl` from its last save, and outlives every browser that closed.

Context: whose a draft is comes from the context of the call, never from the subject the caller
writes, so one user's draft is never read by another for a subject spelled the same — unless
the form says the draft is shared: five users editing one record together open the same draft
with `by=()`. No key is imposed (`Owner.of`); a context that says no tenant and no user saves
its drafts all the same.
"""

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote, unquote

from sincpro_framework.context.domain import exceptions
from sincpro_framework.context.domain.drafts import Draft
from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.context.domain.owner import Owner
from sincpro_framework.context.domain.store import ContextStore, Entry


class Drafts:
    """The drafts of one owner — the values of `by`, read from a context."""

    def __init__(
        self,
        store: ContextStore,
        context: Mapping[Any, Any],
        by: Sequence[str] = Owner.BY_TENANT_AND_USER,
    ) -> None:
        """The drafts saved in `store` for the owner `context` names by the keys of `by` — the
        owner is read once, here, so a later change of the context never moves these drafts.
        """
        self.store = store
        self.owner = Owner.of(context, by)
        """Whose these drafts are: `tenant_id=acme,tenant_ids=,user_id=ana`."""
        self.folder = f"{ContextKey.ENTITY_DRAFT_CONTEXT}:{self.owner}"

    def _key(self, subject: str) -> str:
        """The entry key of `subject`'s draft — the subject encoded, so a `:` in it never
        splits the folder.

            _key("billing.Invoice:7")
            ->  "SINCPRO_ENTITY_DRAFT_CONTEXT:<owner>:billing.Invoice%3A7"
        """
        return f"{self.folder}:{quote(subject, safe='')}"

    def _draft(self, subject: str, entry: Entry) -> Draft:
        """The `Draft` an entry holds: its values and origin version from the entry's values,
        its version and save time from the entry itself."""
        return Draft(
            key=subject,
            values=entry.values["values"],
            version=entry.version,
            origin_version=entry.values["origin_version"],
            kept_at=entry.saved_at,
        )

    def save(
        self, subject: str, values: Mapping[str, Any], origin_version: int, expected: int
    ) -> Draft:
        """`values` as the draft of `subject`, under a new version.

            in      "invoice:7", {"discount": Decimal("50")}, origin 3, expected 0
            out     Draft(version=…, origin_version=3, values={"discount": "50"})

        `expected` is the version the caller read, `0` for none; anything else raises
        `DraftConflict`. The TTL starts again. The draft answered is the one `read` answers
        until the next save.
        """
        try:
            saved = self.store.save_entry(
                self._key(subject),
                {"values": dict(values), "origin_version": origin_version},
                expected,
            )
        except exceptions.EntryConflict as conflict:
            raise exceptions.DraftConflict(
                subject, conflict.expected, conflict.current
            ) from None
        return self._draft(subject, saved)

    def read(self, subject: str) -> Draft | None:
        """The live draft of `subject`, or `None` when there is none, it was discarded or it
        expired."""
        found = self.store.get_entry(self._key(subject))
        return None if found is None else self._draft(subject, found)

    def discard(self, subject: str) -> None:
        """Drop the draft of `subject`; a tab still holding it cannot bring it back. One with
        none is not an error."""
        self.store.delete_entry(self._key(subject))

    def subjects(self) -> list[str]:
        """What this owner has a live draft of, the most recently saved first."""
        return [
            unquote(key.rsplit(":", 1)[1]) for key in self.store.list_entries(self.folder)
        ]
