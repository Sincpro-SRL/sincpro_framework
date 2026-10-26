"""`Owner`: whose something the context saves is — a draft, a pipeline run, a cached answer, an
idempotent write — read from the context of the call, by exactly the keys its component asks
for.

    Owner.of(context, Owner.BY_TENANT_AND_USER)   tenant_id=acme,tenant_ids=,user_id=ana   a user's
    Owner.of(context, Owner.BY_TENANT)            tenant_id=acme,tenant_ids=               a tenant's
    Owner.of(context, ["session_id"])             session_id=s-91                          a session's
    Owner.of(context, ())                         (empty)                                  everybody's

Context: what is saved for someone must never reach someone else, so whose it is comes from the
context of the call — never from a key the caller writes. Which keys make an owner is the
component's choice, and the project's: a draft of one record that five users edit together is
owned by nobody (`()`), a cart by its session, an invoice draft by its user within its tenant.
No key is imposed: a service with no tenants and no users keys by its own ids, and is never
refused.

**Values are encoded** (`urllib.parse.quote`): a `/`, a `,`, a `=`, a `:` or a `|` inside a
value is never read as a separator, so `acme/user_id=bob` is never the user `bob` of `acme`. A
list is one owner of all its members, in sorted order.
"""

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar
from urllib.parse import quote

from sincpro_framework.context.domain.keys import ContextKey


class Owner:
    """Whose something saved is — the keys that name an owner, and the owner a context names by
    them."""

    BY_TENANT: ClassVar[tuple[str, ...]] = (ContextKey.TENANT_ID, ContextKey.TENANT_IDS)
    """A tenant's — or, acting across tenants, all of them: what a pipeline run belongs to."""

    BY_TENANT_AND_USER: ClassVar[tuple[str, ...]] = (*BY_TENANT, ContextKey.USER_ID)
    """A user's within a tenant: what a draft, a cached answer and an idempotent write belong
    to, unless their component is told otherwise."""

    @staticmethod
    def _written(value: Any) -> str:
        """One value as an owner writes it: encoded, a list as its sorted members joined by `|`,
        and empty when the context said nothing.

            _written("acme/x")             ->  "acme%2Fx"
            _written(["globex", "acme"])   ->  "acme|globex"
            _written(None)                 ->  ""
        """
        if value in (None, "", [], ()):
            return ""
        if isinstance(value, (list, tuple, set, frozenset)):
            return "|".join(sorted(quote(str(one), safe="") for one in value))
        return quote(str(value), safe="")

    @classmethod
    def of(cls, context: Mapping[Any, Any], by: Sequence[str]) -> str:
        """Whose something saved is: each key of `by` with what the context says of it.

        Context: the owner is a section of a key in the store —
        `SINCPRO_ENTITY_DRAFT_CONTEXT:<owner>: <subject>` — so two contexts give the same owner
        only when they say the same thing of the same keys. A key the context does not say still
        takes part, empty: a user's draft and a draft of nobody never share a section.

            Owner.of({"tenant_id": "acme", "user_id": "ana"}, Owner.BY_TENANT_AND_USER)
            ->  "tenant_id=acme,tenant_ids=,user_id=ana"
            Owner.of({"tenant_ids": ["globex", "acme"]}, Owner.BY_TENANT)
            ->  "tenant_id=,tenant_ids=acme|globex"
            Owner.of({"tenant_id": "acme"}, ())
            ->  ""

        1. Each key of `by` once, in the order given.
        2. Its value as the context says it, written by `_written`.
        Final: `key=value` pairs joined by commas — empty for no keys: everybody's.
        """
        keys = dict.fromkeys(by)
        return ",".join(
            f"{quote(key, safe='')}={cls._written(context.get(key))}" for key in keys
        )
