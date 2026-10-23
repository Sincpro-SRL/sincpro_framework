"""Whose something the context keeps is — a draft, a pipeline run — read from the context, always in
one shape:

    tenant_id=acme,tenant_ids=,user_id=ana            a user in a tenant
    tenant_id=,tenant_ids=acme|globex,user_id=ana     a user across tenants
    tenant_id=,tenant_ids=,user_id=                   a service with no tenant and no user

**The tenant keys are always there**, then the keys of `by`, each with what the context says — or
nothing, when it says `None`, empty, or nothing at all. A project with no tenants and no users keys
what it keeps by its own id alone, and is never refused for it. One shape for every system means no
two contexts give the same owner unless they say the same thing.

**Values are encoded** (`urllib.parse.quote`): a `/`, a `,`, a `=`, a `:` or a `|` inside a tenant or
a user is never read as a separator, so `acme/user_id=bob` is never the user `bob` of `acme`.
A list is one owner of all its members, in sorted order.
"""

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from sincpro_framework.context.domain.keys import ContextKey


def owner_of(context: Mapping[Any, Any], by: Sequence[str]) -> str:
    """The owner the context says: its tenant keys, then the keys of `by`, each `key=value`."""
    said: list[str] = []
    for key in dict.fromkeys((ContextKey.TENANT_ID, ContextKey.TENANT_IDS, *by)):
        value = context.get(key)
        if value in (None, "", [], ()):
            text = ""
        elif isinstance(value, (list, tuple)):
            text = "|".join(sorted(quote(str(one), safe="") for one in value))
        else:
            text = quote(str(value), safe="")
        said.append(f"{quote(key, safe='')}={text}")
    return ",".join(said)
