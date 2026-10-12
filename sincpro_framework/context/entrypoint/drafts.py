"""`Drafts`: the drafts of whoever the context says — a user, a session, any keys a project chooses.

    use_context().drafts()                          this user's — within this tenant when there is one
    use_context().drafts(by=["session_id"])         this session's
    use_context().drafts(by=["user_id", "pos_id"])  this user's on this point of sale

Context: whose a draft is comes from the context of the call, never from the key the caller writes,
so one user's draft is never read by another for a key spelled the same. A tenant in the context
always takes part: a draft never crosses tenants. A key `by` names that the context does not have is
refused (`ContextRequired`) — a draft nobody owns would be everybody's.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from sincpro_framework.context.domain.drafts import Draft, DraftStore
from sincpro_framework.context.domain.keys import TENANT_ID, USER_ID
from sincpro_framework.exceptions import ContextRequired

BY_USER = (USER_ID,)
"""Whose a draft is unless told otherwise: the user's."""


class Drafts:
    """The drafts of one owner — the values of `by`, and the tenant, read from a context."""

    def __init__(
        self, store: DraftStore, context: Mapping[Any, Any], by: Sequence[str] = BY_USER
    ) -> None:
        missing = [key for key in by if context.get(key) in (None, "")]
        if missing:
            raise ContextRequired("drafts", missing)
        tenant = context.get(TENANT_ID)
        named = ([] if tenant in (None, "") or TENANT_ID in by else [TENANT_ID]) + list(by)
        self.store = store
        self.owner = "/".join(f"{key}={context[key]}" for key in named)
        """Whose these drafts are: `tenant_id=acme/user_id=ana`."""

    def keep(
        self, subject: str, values: Mapping[str, Any], origin_version: int, expected: int
    ) -> Draft:
        """`values` as the draft of `subject` — see `DraftStore.keep`."""
        return self.store.keep(self.owner, subject, values, origin_version, expected)

    def read(self, subject: str) -> Draft | None:
        return self.store.read(self.owner, subject)

    def discard(self, subject: str) -> None:
        self.store.discard(self.owner, subject)

    def subjects(self) -> list[str]:
        """What this owner has a live draft of, the most recently kept first."""
        return self.store.subjects(self.owner)
