"""`ApprovalRequests`: the approvals of whoever the context says, stored in the context's store.

    requests = ApprovalRequests.of()                       this tenant's and user's
    requests.open("sales.CommandCreateQuotation", {...}, Preview(text="…"), timedelta(minutes=15))
    requests.read("9fK…")
    requests.find(state=ApprovalState.PENDING)

An approval is an entry of the context's store, under one namespace and one owner:

    SINCPRO_APPROVAL_CONTEXT : tenant_id=acme,tenant_ids=,user_id=ana : 9fK…
    └─ namespace               └─ owner (`Owner.of`)                    └─ the approval

Every write names the version it read, so of two that decide the same approval at once — on two
replicas — one wins and the other reads what it decided (`EntryConflict`). While the store does
not answer, an approval can be neither asked nor decided (`StoreUnavailable`): a decision cannot
pretend to be stored.
"""

import secrets
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any
from urllib.parse import quote

from sincpro_framework.approvals.domain.approval import ApprovalRecord, ApprovalState, Preview
from sincpro_framework.context import (
    Context,
    ContextKey,
    ContextStore,
    Entry,
    Owner,
    use_context,
)

utc_now = partial(datetime.now, UTC)
"""The clock approvals are judged by — aware, in UTC; a test hands its own."""


class ApprovalRequests:
    """The approvals of one owner — the values of `by`, read from a context: the tenant's and the
    user's by default."""

    def __init__(
        self,
        store: ContextStore,
        context: Mapping[Any, Any],
        by: Sequence[str] = Owner.BY_TENANT_AND_USER,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        """The approvals stored in `store` for the owner `context` names by the keys of `by` —
        read once, here; `now` is the clock they are judged by."""
        self.store = store
        self.owner = Owner.of(context, by)
        """Whose these approvals are: `tenant_id=acme,tenant_ids=,user_id=ana`."""
        self.now = now

    @classmethod
    def of(
        cls,
        context: Context | None = None,
        by: Sequence[str] = Owner.BY_TENANT_AND_USER,
        store: ContextStore | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> "ApprovalRequests":
        """The approvals of whoever `context` says — the context in play when none is given — in
        `store`, else where that context stores what it owns.

            ApprovalRequests.of()                           ->  this tenant's and user's
            ApprovalRequests.of(by=Owner.BY_TENANT)         ->  anyone's of this tenant
        """
        read = context if context is not None else use_context()
        return cls(read.store_in_use(store), read, by, now)

    def _folder(self) -> str:
        """The folder of this owner's approvals: `SINCPRO_APPROVAL_CONTEXT:<owner>`."""
        return f"{ContextKey.APPROVAL_CONTEXT}:{self.owner}"

    def _key(self, approval_id: str) -> str:
        """Where one approval is stored — its id encoded, so a `:` in it never splits the key."""
        return f"{self._folder()}:{quote(approval_id, safe='')}"

    def _record(self, entry: Entry) -> ApprovalRecord:
        """The `ApprovalRecord` an entry holds, as it stands now, its `store_version` the entry's
        version — what the next `save` names as `expected`."""
        found = ApprovalRecord.model_validate(
            {**entry.values, "store_version": entry.version}
        )
        return found.seen_at(self.now())

    def save(self, record: ApprovalRecord, expected: int) -> ApprovalRecord:
        """`record` under a new version, when `expected` is the version there now — `0` for
        none; anything else raises `EntryConflict`."""
        owned = record.model_copy(update={"owner": self.owner})
        saved = self.store.save_entry(
            self._key(record.approval_id),
            owned.model_dump(mode="json", exclude={"store_version"}),
            expected,
        )
        return self._record(saved)

    def open(
        self, use_case: str, input: dict[str, Any], preview: Preview, expires_in: timedelta
    ) -> ApprovalRecord:
        """A new approval of `use_case` for `input`, pending until `expires_in` from now — its id
        random (`secrets.token_urlsafe`), never guessed."""
        now = self.now()
        record = ApprovalRecord(
            approval_id=secrets.token_urlsafe(24),
            use_case=use_case,
            input=input,
            preview=preview,
            requested_at=now,
            expires_at=now + expires_in,
        )
        return self.save(record, 0)

    def read(self, approval_id: str) -> ApprovalRecord | None:
        """The approval as it stands now, or `None` when there is none, it is someone else's or
        the store let it go."""
        found = self.store.get_entry(self._key(approval_id))
        return None if found is None else self._record(found)

    def find(
        self, state: ApprovalState | None = None, limit: int = 100
    ) -> list[ApprovalRecord]:
        """The approvals of this owner, the most recently asked first — those in `state` when
        named."""
        found = [
            self._record(entry)
            for key in self.store.list_entries(self._folder())
            if (entry := self.store.get_entry(key)) is not None
        ]
        chosen = [one for one in found if state is None or one.state is state]
        chosen.sort(key=lambda one: one.requested_at, reverse=True)
        return chosen[:limit]
