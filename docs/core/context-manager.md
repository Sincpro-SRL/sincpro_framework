# The context: one component, read from anywhere, scoped, typed, carried, kept when asked

The context says **who** a request acts for, **for which tenant**, **in which language**, **in which
flow** — and anything a project adds. It reaches every Feature, ApplicationService, hook, interceptor
and adapter without a parameter, crosses threads, buses, services and brokers, and can be kept in a
store for whoever resumes the flow. Why it is shaped this way: [PRD_22](../prd/PRD_22_context-component.md).

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## The syntax you already have

```python
from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.context import EntrypointKind, Level, use_context


class CommandConfirmSale(DataTransferObject):
    sale_id: str


class CommandCheckCredit(DataTransferObject):
    pass


sales = UseFramework("sales-context", log_after_execution=False)
seen: list = []


@sales.feature(CommandCheckCredit)
class CheckCredit(Feature):
    def execute(self, dto: CommandCheckCredit) -> None:
        seen.append(self.context)                      # the bus's context, a dict


@sales.app_service(CommandConfirmSale)
class ConfirmSale(ApplicationService):
    def execute(self, dto: CommandConfirmSale) -> None:
        seen.append(use_context())                     # the same, from anywhere
        self.feature_bus.execute(CommandCheckCredit())


with sales.context({"tenant_id": "acme", "user_id": "ana", "lang": "es"}):
    sales(CommandConfirmSale(sale_id="S-1"))

confirm, check = seen
assert check["tenant_id"] == "acme" and check.get("tz", "America/La_Paz") == "America/La_Paz"
assert isinstance(check, dict)                         # json.dumps, **context, as always
```

`with bus.context({...})`, nested `with`, `self.context`, `bus.current_context()` and
`global_scope=True` work as they always did. What is new is underneath, and what you can reach.

## A tree of nodes, never one context overwritten

```
ROOT          the process                       defaults, flags, settings
 └─ BUS        what a bounded context publishes  bus.context(..., global_scope=True)
     └─ ENTRYPOINT  what the entrance knew        who, tenant, lang, the flow — and its kind
         └─ APPLICATION  an ApplicationService    each execution, a node of its own
             └─ FEATURE   a Feature
                 └─ HOOK  a repository's hook
                 SCOPE    a block, wherever it is opened
```

Each node keeps only its own values and a pointer to its parent. **Reading walks up and the nearest
node wins** — a React provider's rule. Closing a node is returning to its parent.

```python
assert [one.level for one in check.lineage()] == [
    Level.FEATURE,
    Level.APPLICATION,
    Level.ENTRYPOINT,
    Level.BUS,
    Level.ROOT,
]
assert check.entrypoint is not None and check.entrypoint.kind is EntrypointKind.DIRECT
assert check.application is not None and check.application.label == "CommandConfirmSale"
assert check.origin("tenant_id").level is Level.ENTRYPOINT
```

| You ask | You get |
|---|---|
| `use_context()` | the node in play — a `dict` of what it sees |
| `use_context(Level.ENTRYPOINT)`, `context.at(level)` | that level's node, `None` when there is none above |
| `context.root` · `.bus` · `.entrypoint` · `.application` · `.feature` · `.hook` | the nearest of each |
| `context.parent` · `context.level` · `context.own` | the tree itself |
| `context.origin(key)` | `Origin(level, label, execution_id)` — who said it |
| `context.lineage()` | this node and every one above it |
| `context.execution` | the identity: `execution_id`, `causation_id`, `correlation_id` |

Every entrance opens the flow with its kind — `REST`, `RPC`, `GRPC`, `MCP`, `QUEUE`, `CRON`, `REMOTE`,
or `DIRECT` for a call from code. A Feature never needs it; `context.entrypoint.kind` answers when it
does.

## Writing — two ways, on purpose

```python
class CommandStamp(DataTransferObject):
    pass


orders = UseFramework("orders-context", log_after_execution=False)


@orders.feature(CommandStamp)
class Stamp(Feature):
    def execute(self, dto: CommandStamp) -> None:
        self.context["note"] = "the whole call sees this"     # the scope of the call
        use_context().set("pos_id", 3)                        # this node: what it runs, only


with orders.context({"tenant_id": "acme"}):
    orders(CommandStamp())
    assert orders.current_context()["note"] == "the whole call sees this"
    assert "pos_id" not in orders.current_context()

    with use_context().scoped({"tenant_id": "beta"}) as block:   # a child scope for a block
        assert block["tenant_id"] == "beta"
    assert use_context()["tenant_id"] == "acme"
```

| Write | Lands on | Seen by |
|---|---|---|
| `context[key] = value`, `update`, `del` | the scope of the call — the `bus.context(...)` block, or the call | every execution of that call, as `self.context` always behaved |
| `context.set(key, value)` | the node in play | what it runs afterwards — never its caller or a sibling |
| `context.entrypoint.set(...)`, `context.root.set(...)` | that level, said on purpose | everything under it |

`bus.current_context()` is the same object, read-only. Deleting a key a scope inherited hides it in
that scope only.

**A block with only what a level above says.** `scoped()` opens from any level, not only the one in
play: the block sees that level, without what the nodes under it changed, and stays in the execution
in play (its identity, its cause):

```python
class CommandAudit(DataTransferObject):
    pass


audits = UseFramework("audits-context", log_after_execution=False)
audited: list = []


@audits.feature(CommandAudit)
class Audit(Feature):
    def execute(self, dto: CommandAudit) -> None:
        audited.append(use_context())


@audits.app_service(CommandConfirmSale)
class ConfirmAndAudit(ApplicationService):
    def execute(self, dto: CommandConfirmSale) -> None:
        use_context().set("tenant_id", "beta")              # this service works for beta
        with use_context().parent.scoped():                 # the audit runs with what came in
            self.feature_bus.execute(CommandAudit())


with audits.context({"tenant_id": "acme"}):
    audits(CommandConfirmSale(sale_id="S-2"))

assert audited[0]["tenant_id"] == "acme"
```

`use_context(Level.ENTRYPOINT).scoped()` runs a block with exactly what the entrance knew.

**The flow is what the context says.** Writing `correlation_id` — `context["correlation_id"] = …`,
`set`, or a scope that names it — renames the flow from there on: the execution in play, its signals
and what it runs afterwards all say the new one, and a scope's name ends with the scope.
`execution_id` and `causation_id` are minted by the framework: written mid-execution they are kept as
values and warned about; only an entrance's are taken, for a root.

A value can live under its **type** — what a settings object, a client or a store is found by:

```python
from dataclasses import dataclass


@dataclass
class SiatSettings:
    url: str


use_context().root.set(SiatSettings, SiatSettings(url="https://siat.example"))
with sales.context({"tenant_id": "acme"}):
    assert use_context()[SiatSettings].url == "https://siat.example"
```

## Threads and pools — the same on every Python build

An asyncio task starts from its creator's context. A `ThreadPoolExecutor` hands a worker nothing, and
a plain `Thread` starts empty on the default build. These hand it on everywhere:

```python
from sincpro_framework.context import ContextExecutor, ContextThread, in_context

with sales.context({"tenant_id": "acme"}):
    with ContextExecutor(max_workers=4) as pool:                # every task, the submitter's context
        tenants = list(pool.map(lambda _: use_context()["tenant_id"], range(4)))
    thread = ContextThread(target=lambda: tenants.append(use_context()["tenant_id"]))
    thread.start()
    thread.join()

assert tenants == ["acme"] * 5
```

`in_context(fn)` wraps one function for a pool the project already owns. Nodes are copy-on-write: a
thread never sees another's write half done, and what it `set`s stays in its own node.

## Providers, schemas, requirements, secrets

```python
from enum import IntEnum
from typing import TypedDict

from pydantic import Secret

from sincpro_framework.context import requires_context
from sincpro_framework.exceptions import ContextRequired


class Environment(IntEnum):
    PRODUCTION = 1
    TEST = 2


class SiatContext(TypedDict, total=False):
    TOKEN: Secret[str]
    SIAT_ENV: Environment
    nit_id: str


siat = UseFramework("siat-context", log_after_execution=False)
siat.context_schema(SiatContext)                       # what a scope opens with, typed


@siat.context_provider(needs=["nit_id"], gives=["TOKEN", "SIAT_ENV"])
def credentials(context):                              # "I want context": derived once per scope
    return {"TOKEN": Secret(f"token-of-{context['nit_id']}"), "SIAT_ENV": 2}


class CommandSendInvoice(DataTransferObject):
    pass


@siat.feature(CommandSendInvoice)
@requires_context("TOKEN", "SIAT_ENV")                 # declared: refused when missing
class SendInvoice(Feature):
    def execute(self, dto: CommandSendInvoice) -> None:
        seen.append(self.context)


with siat.context({"nit_id": "N-1"}):
    siat(CommandSendInvoice())

sent = seen[-1]
assert sent["TOKEN"].get_secret_value() == "token-of-N-1"
assert sent.to_client()["TOKEN"] == "token-of-" + sent["nit_id"]   # it travels, as its value

try:
    siat(CommandSendInvoice())                          # no nit_id: nothing provides the token
except ContextRequired as refused:
    assert refused.missing == ["TOKEN", "SIAT_ENV"]
```

- **A provider** runs when an execution opens, has what it `needs` and lacks what it `gives`; its
  answer lands on that execution's node, so what it runs finds it and it is not asked again.
- **A schema** validates and types the keys it names — `SIAT_ENV=2` arrives a `SIATEnvironment`; any
  other key passes as given.
- **A requirement** is a declaration: refused with `ContextRequired` when missing. A use case that
  declares nothing is never checked.
- **A `Secret`** travels like any value — the framework filters nothing; what goes in the context
  is the project's decision. An API key the next context needs reaches it.

## Shared across services: the same tree, kept in a store

A bus that says so keeps its context in a store — Redis through `KeyValueContexts(RedisKeyValue(redis))`.
The API does not change: `self.context`, `use_context()`, `bus.context(...)` and the levels read and
write as always; only where each node is kept changes. Nothing is shared by default, and no
environment variable turns it on.

```text
billing.context_store(KeyValueContexts(RedisKeyValue(redis)))      each bus that shares, in code

global          use_context().at(Level.GLOBAL) — every service and replica that set the store
 └ process      this service's replicas — never another service
    └ bus       local
       └ entrypoint → application → feature → hook → scope    one node per execution, kept
```

- **Across a queue or the remote bus only an id travels** (`x-context-node`, beside the identity):
  the receiving bus that set the same store opens under the sender's nodes, read back from the
  store. What service A's ApplicationService wrote — `self.context["discount"] = 10` — service B's
  Feature reads with `self.context.get("discount")`, even when A wrote it after handing on.
- **The sender's nodes are read, never written**; B's writes land on B's nodes, by the rules above.
- **No collision**: every node has its own id (the execution's), so five requests are five chains;
  the process is kept per service; only `Level.GLOBAL` is one for everyone.
- Two projects that set the same store (same prefix) share as well; the default codec is plain JSON,
  so they need no classes in common.
- `context_store(store, ttl=timedelta(hours=24), every=timedelta(seconds=1))`: a node lives `ttl`;
  what others wrote is read again at most once per `every`.

See `docs/prd/PRD_24_shared-context.md`.

## Kept outside: N contexts in a store

```python
from datetime import timedelta

from sincpro_framework.caching import InMemoryKeyValue
from sincpro_framework.context import ContextStore, KeyValueContexts

contexts = KeyValueContexts(InMemoryKeyValue())         # RedisKeyValue(redis) in production
use_context().root.set(ContextStore, contexts)          # the process's; bus.context_store(...) for a bus

contexts.keep("tenant:acme", {"lang": "es", "tz": "America/La_Paz"})
contexts.keep("session:s1", {"user_id": "ana", "lang": "en"}, ttl=timedelta(hours=8))

with sales.context({"plan": "pro"}, restore=["tenant:acme", "session:s1"]):     # in order
    restored = use_context()
    assert (restored["lang"], restored["tz"], restored["user_id"]) == (
        "en",
        "America/La_Paz",
        "ana",
    )

with sales.context({"tenant_id": "acme"}, keep_as="sale-77", ttl=timedelta(hours=1)):
    pass
with sales.context(restore="sale-77"):                  # a worker, a cron, another service
    assert use_context()["tenant_id"] == "acme"
```

A store keeps **what a flow is** — a tenant's defaults, a session, a flow to resume — not what can be
recomputed (that is a cache). `InMemoryContexts` for one process; `KeyValueContexts` over any
`KeyValueStore` — Redis, Valkey, Memcached — for every replica and service. How it is written is a
codec: `PlainCodec` (JSON any language reads, the default), `TypedCodec(SiatContext)` (the types back,
the same codebase), `PickleCodec` (anything picklable, never bytes from outside — each value on its own: a reader
lacking a value's class reads the context without that key, with a warning).

**The process level, shared by every replica**:

```python
use_context().root.share(KeyValueContexts(InMemoryKeyValue()), every=timedelta(seconds=5))
use_context().root.set("maintenance", False)            # every replica reads it within 5 s
```

Read locally, read again only when the store's version moved — never a network call per read.

## Across services, brokers and the frontend

| Transport | How the context crosses |
|---|---|
| A bus calling another, one process | the whole tree: the callee runs under the caller's node |
| `remote_execution` (HTTP, gRPC) | the whole context of the caller as one packed header, caused by the calling execution |
| A broker (FastStream), an HTTP or gRPC entrance | `inject` / `extract`: `baggage` (W3C), `sincpro-context` (JSON, lists too), the identity headers |
| The frontend | `use_context().to_client()` out; the `sincpro-context` header back, read by every HTTP entrance |

```python
from sincpro_framework.context.adapters.propagation import extract, inject


class CommandPublish(DataTransferObject):
    pass


messages = UseFramework("messages-context", log_after_execution=False)


@messages.feature(CommandPublish)
class Publish(Feature):
    def execute(self, dto: CommandPublish) -> None:
        seen.append(inject(use_context()))               # what a message or a request carries


with messages.context({"tenant_id": "acme", "tenant_ids": ["acme", "beta"]}):
    messages(CommandPublish())

carried = extract(seen[-1])                             # what the other side opens its flow with
assert carried["tenant_ids"] == ["acme", "beta"] and "causation_id" in carried
```

Only what is simple travels: text, numbers, booleans and lists of them, and a `Secret` as the value
it holds. An object or a connection stays in its process, with a warning the first time.

## Execution identity

Every execution knows who it is, what caused it and which flow it belongs to
([PRD_21](../prd/PRD_21_execution-identity.md)) — the same three attributes and the same rule a
`DomainEvent` carries, one id space:

| Key | Value |
|---|---|
| `execution_id` | a UUID v7 of its own; its start is read off it |
| `causation_id` | the execution that called it, or the event that started it |
| `correlation_id` | the flow: the cause's, else the cause's own id, else its own |

They are on every log line, span and GlitchTip event of the execution, and on `context.execution`. A
root execution takes what it was handed (`bus.context({...})`, `x-correlation-id` /
`x-causation-id` / `x-execution-id`); a bus mints with another generator when told
(`bus.execution_ids(fn)`). An event recorded, published or saved inside an execution joins its chain.

## The keys the framework reads

| Key | Meaning |
|---|---|
| `user_id` | who the execution acts for |
| `tenant_id` | the tenant it acts in |
| `tenant_ids` | every tenant it may act across — a list |
| `correlation_id`, `causation_id`, `execution_id` | the identity |

`"user.id"` and `"tenant"` are still read, as `user_id` and `tenant_id`, with a warning the first time;
the key given stays where it was. The signals keep their own names: logs, spans and metrics say
`tenant` and `user_id`.
