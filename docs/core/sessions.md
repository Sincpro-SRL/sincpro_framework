# Sessions: one sign-in that lasts while it is used

A session is what a backend keeps for a client that signed in: who it is, the token of the system
behind it, whatever the next request needs. The client holds only its id — a cookie, a bearer — and
sends it back. A client that keeps using its session is never asked to sign in again; one abandoned
lapses on its own.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Opening, reading, signing out

```python
from datetime import timedelta

from sincpro_framework import UseFramework
from sincpro_framework.context import KeyValueContexts, use_context
from sincpro_framework.data_layer.store import InMemoryKeyValue

mcp = UseFramework("sessions-mcp", log_after_execution=False)
mcp.store_context_in(KeyValueContexts(InMemoryKeyValue()))   # RedisKeyValue.from_url(url) on replicas

with mcp.context({"tenant_id": "acme"}):
    sessions = use_context().sessions()                  # 30 days unused, renewed at every use
    opened = sessions.open({"user_id": "ana", "odoo_token": "t0k"})

    found = sessions.get(opened.id)                      # its expiry moved: 30 more days
    assert found is not None and found.values["user_id"] == "ana"

    sessions.save(opened.id, {**found.values, "company_id": 3})   # same id, same opening
    sessions.close(opened.id)                            # signed out, on every replica
    assert sessions.get(opened.id) is None
```

The id is `secrets.token_urlsafe(32)`: random, never guessed. The values are JSON — text, numbers,
booleans, lists and mappings of them.

## How long a session lasts

| `use_context().sessions(...)` | Lasts |
|---|---|
| `()` | 30 days unused; while used, without limit |
| `(idle_for=timedelta(hours=8))` | 8 hours unused |
| `(idle_for=None)` | until closed |
| `(timedelta(hours=8), at_most=timedelta(days=7))` | 8 hours unused, and never past 7 days from its opening however much it is used |

**Sliding, not fixed.** Every `get` gives the session `idle_for` more with `KeyValueStore.touch` — a
new expiry, its value not written again: Redis `PEXPIRE`, Memcached `touch`, a DynamoDB
`UpdateItem`. `Session.expires_at` says when it lapses if nobody uses it again; `None`, never.

## A code used once

An OAuth authorization code, a magic link, a one-time token: `take` reads it and lets go of it in
one step, so of many callers racing for it exactly one gets it.

```python
with mcp.context({"tenant_id": "acme"}):
    codes = use_context().sessions(idle_for=timedelta(minutes=5), namespace="oauth-codes")
    code = codes.open({"client_id": "claude", "redirect_uri": "https://claude.ai/callback"})

    assert codes.take(code.id) is not None
    assert codes.take(code.id) is None                   # used once
```

## Where they live

On the `KeyValueStore` the context is stored on — the bounded context's own (`bus.store_context_in`),
else the process's (`store_context_in`), else this process's memory — under
`session:<namespace>:<id>`. Every replica on the store reads the same session; `namespace` keeps
apart the sessions of different purposes on one store. `Sessions(store, idle_for, at_most,
namespace, now)` builds them on any `KeyValueStore`, with a clock of the test's own.

**While the store does not answer**, a session can be neither read nor opened (`StoreUnavailable`):
a login cannot pretend to be known. Everything else the context does goes on in memory (see
[the context](context-manager.md#when-the-store-is-down)).
