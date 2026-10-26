# The context: one component, read from anywhere, scoped, typed, carried, stored when asked

The context says **who** a request acts for, **for which tenant**, **in which language**, **in which
flow** — and anything a project adds. It reaches every Feature, ApplicationService, hook, interceptor
and adapter without a parameter, crosses threads, buses, services and brokers, and can be persisted in a
store for whoever resumes the flow. Why it is shaped this way: [PRD_22](../prd/PRD_22_context-component.md).

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## The syntax you already have

```python
from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.context import EntrypointKind, ContextLevel, use_context


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

Each node holds only its own values and a pointer to its parent. **Reading walks up and the nearest
node wins** — a React provider's rule. Closing a node is returning to its parent.

Every moment of a repository's hook runs in a `HOOK` node of its own, so what a hook writes never
lands on the Feature that called the repository. The moments of a reading — `after_read`, once per
row, and `after_search`, once per page — keep that node in this process: a bus that shares its
context saves no node per row read.

```python
assert [one.level for one in check.lineage()] == [
    ContextLevel.FEATURE,
    ContextLevel.APPLICATION,
    ContextLevel.ENTRYPOINT,
    ContextLevel.BUS,
    ContextLevel.ROOT,
]
assert check.entrypoint is not None and check.entrypoint.kind is EntrypointKind.DIRECT
assert check.application is not None and check.application.label == "CommandConfirmSale"
assert check.origin("tenant_id").level is ContextLevel.ENTRYPOINT
```

| You ask | You get |
|---|---|
| `use_context()` | the node in play — a `dict` of what it sees |
| `use_context(ContextLevel.ENTRYPOINT)`, `context.at(level)` | that level's node, `None` when there is none above |
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

`use_context(ContextLevel.ENTRYPOINT).scoped()` runs a block with exactly what the entrance knew.

**The flow is what the context says.** Writing `correlation_id` — `context["correlation_id"] = …`,
`set`, or a scope that names it — renames the flow from there on: the execution in play, its signals
and what it runs afterwards all say the new one, and a scope's name ends with the scope.
`execution_id` and `causation_id` are minted by the framework: written mid-execution they are stored as
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
from sincpro_framework import exceptions


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
except exceptions.ContextRequired as refused:
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

## Shared across services: the same tree, persisted in a store

Each level is stored where the one who owns it says: the process in the process's store, a bounded
context in its own. Redis through `KeyValueContexts(RedisKeyValue(redis))`. The API does not change:
`self.context`, `use_context()`, `bus.context(...)` and the levels read and write as always; only
where each one is stored changes. Nothing is shared by default, and no environment variable turns
it on.

```text
store_context_in(KeyValueContexts(RedisKeyValue(redis)))                  the process, in code
billing.store_context_in(KeyValueContexts(RedisKeyValue(billing_redis)))  a bounded context, in code

global          "global"             the process's store — every service that set it
 └ process      "process:<service>"  the process's store — this service's replicas
    └ bus       "bus:<name>"         the bounded context's store — every pod of it
       └ entrypoint → application → feature → hook → scope
                "node:<id>"          the bounded context's store — one node per execution
```

- **The process and the global are the process's**, shared from the first execution of any bus once
  `store_context_in(...)` is set — never through a bounded context's store.
- **A bounded context's own level is read by all its pods**: what `with billing.context({...},
  global_scope=True)` or `use_context().bus.set(...)` writes in one pod, every pod of billing reads
  within `every`. A `global_scope` block puts back only its own keys when it ends.
- **Across a queue or the remote bus the flow travels twice** — as the headers' snapshot, and as
  the id of the node in play (`x-context-node`). A receiving bus that set the same store opens under
  the sender's nodes, read back from the store: what service A's ApplicationService wrote —
  `self.context["discount"] = 10` — service B's Feature reads with `self.context.get("discount")`,
  even when A wrote it after handing on. With a store of its own it reads the snapshot (see
  "What crosses a process").
- **The sender's nodes are read, never written**; B's writes land on B's nodes, by the rules above.
- **No collision**: every node has its own id (the execution's), so five requests are five chains;
  the process is stored per service; only `ContextLevel.GLOBAL` is one for everyone.
- Two projects that set the same store (same prefix) share as well; the default codec is plain JSON,
  so they need no classes in common.
- `bus.store_context_in(store, ttl=timedelta(hours=24), every=timedelta(seconds=1))`: a node lives `ttl`;
  what others wrote is read again at most once per `every`.

See `docs/prd/PRD_24_shared-context.md`.

## A Redis of its own per bounded context

A bounded context may grow into a service of its own, so everything it stores can live on a store
of its own — its level, its executions, its caches, idempotency, drafts, pipeline runs, leases —
while the rest stays on the process's. The address is configuration, by the settings cascade: a
shared field every context inherits, overridden by the context that needs its own.

```python
from sincpro_framework.settings import SincproConfig


class SharedSettings(SincproConfig):
    redis_url: str = "redis://localhost:6379/0"


class BillingSettings(SharedSettings): ...


class ServiceSettings(SharedSettings):
    billing: BillingSettings
```

```yaml
my_service:
  redis_url: $ENV:REDIS_URL
  billing:
    redis_url: $ENV:BILLING_REDIS_URL      # absent: billing inherits the shared one
```

```python
from sincpro_framework import UseFramework
from sincpro_framework.context import KeyValueContexts, store_context_in
from sincpro_framework.data_layer.store import InMemoryKeyValue

# the service's wiring: the process level, the global, every context without a store of its own
store_context_in(InMemoryKeyValue())   # RedisKeyValue(redis.Redis.from_url(settings.redis_url))

# billing/infrastructure/dependencies.py — billing's own, beside its database
# (Repository(Database(settings.billing.database_url)))
invoicing = UseFramework("invoicing", log_after_execution=False)
invoicing.store_context_in(
    KeyValueContexts(InMemoryKeyValue())   # RedisKeyValue(redis.Redis.from_url(settings.billing.redis_url))
)
```

| What | Without `billing.store_context_in` | With it |
|---|---|---|
| `bus.cache`, `bus.idempotency`, `bus.cache.queries` | the process's store, keyed by `billing` | billing's store |
| drafts, pipeline runs, leases, circuits in billing's executions | the process's store | billing's store |
| billing's own level (`bus:billing`) | in each pod | billing's store — every pod reads it |
| billing's execution nodes, handed on by id | not stored | billing's store |
| the process level and the global | the process's store | the process's store |

Moving billing out of the service later changes none of its code: its store already holds only
what is billing's.

## Where the context is stored

The context needs no saving and no loading by hand to reach another process: whatever a use case
hands on carries it — a published event, a message to a queue, a call to another service, an answer
to the frontend — and the entrance on the other side opens its flow with it (see "Across services"
below). What *is* stored is what the framework stores for the context, in one store a project sets once:

```python
from datetime import timedelta

from sincpro_framework.context import ContextStore, KeyValueContexts, store_context_in
from sincpro_framework.data_layer.store import InMemoryKeyValue

contexts = store_context_in(KeyValueContexts(InMemoryKeyValue()))   # RedisKeyValue(redis) in production
assert use_context().root.get(ContextStore) is contexts             # the process's
sales.store_context_in(contexts)                                    # or one bus: its own store
```

What a `KeyValueContexts` writes on Redis, under its prefix (`sincpro:context` by default):

| Key | What | Lives |
|---|---|---|
| `global`, `process:<service>` | the global and the process level | until deleted |
| `bus:<name>` | a bounded context's own level | until deleted |
| `node:<id>`, `node:<id>:meta` | one node of an execution: its values, its parent, level and label | the bus's `ttl` (24 h) |
| `…:version` beside each | moved on every write, with the key's ttl — what a reader compares | as its key |
| `entries:…` | drafts and pipeline runs, each with a version | `entry_ttl` (7 days) from its last save |
| `lease:…` | leases: a name held by one owner at a time | the lease's own time |

Beside them, on the same `KeyValueStore`, in sections of their own: `bus.cache`, `bus.idempotency`,
the query cache, the circuit breakers (`sincpro:circuit_breaker:<name>:…`) and the
[sessions](sessions.md) (`session:<namespace>:<id>`). **None of it is ever
a value of the context**: the context holds the keys that address it — a tenant, a user, a node's
id — and the data stays in the store. A cache is keyed by its owner (`tenant_id`, `user_id`, the
keys of `vary_by`) read from the context of the call; a draft by the keys `by=` names.

The store holds, each under a prefix of its own and each reached from the context — never as values
inside a node, which would be copied at every read and die with their scope: the [drafts](drafts.md)
a form stores between requests (`use_context().drafts()`), and how far each run of a
[pipeline](pipelines.md) got (`PipelineRuns.of()`). Whose they are comes from the context, by
exactly the keys each one names — a user within a tenant for a draft, a tenant for a run, nobody for
a draft of a record everyone edits (`by=()`) — and no shape of system is refused.

Beside them, on the `KeyValueStore` the store stands on (`key_value_store()`), each in a section of
its own: the [leases](leases.md), the [circuit breakers](circuit-breaker.md), and every bounded context's cache and
idempotency — `bus.cache`, `bus.idempotency` ([caching](../caching/README.md#the-simple-way-buscache-and-busidempotency-nothing-to-build)),
keyed apart by the bus's name. A bus holds them on its own store (`bus.store_context_in(...)`), else on
the process's — `store_context_in(redis)` sets it in one line, the same as
`use_context().root.set(ContextStore, KeyValueContexts(redis))` — else in this process's memory.

A store persists **what a flow is** — a tenant's defaults, a session, a flow to resume — not what can be
recomputed (that is a cache). `InMemoryContexts` for one process; `KeyValueContexts` over any
`KeyValueStore` — Redis, Valkey, Memcached — for every replica and service. How it is written is a
codec: `PlainCodec` (JSON any language reads, the default), `TypedCodec(SiatContext)` (the types back,
the same codebase), `PickleCodec` (anything picklable, never bytes from outside — each value on its own: a reader
lacking a value's class reads the context without that key, with a warning).

**The process level, shared by every replica**:

```python
use_context().root.share(KeyValueContexts(InMemoryKeyValue()), "process:billing", every=timedelta(seconds=5))
use_context().root.set("maintenance", False)            # every replica reads it within 5 s
```

Read locally, read again only when the store's version moved — never a network call per read. A
write lands on the latest values (`ContextStore.change`, compared before it writes): a replica
writing `plan` never erases the `maintenance` another wrote. A default the code seeds is added when
the store lacks it, so one a later release brings reaches every replica, and a value a replica saved
wins. A value that does not travel — an object, a settings instance — stays in its process and is
never dropped by a refresh. The same holds for `ContextLevel.GLOBAL`.

**A store of its own per test.** The process level and everything stored on it — `bus.cache`,
`bus.idempotency`, drafts, sessions, leases — belong to the interpreter, so a test suite shares them
unless each test opens its own:

```python
from sincpro_framework.runtime.testing import fresh_context

with fresh_context() as kept:                                 # a new memory, its own caches
    assert use_context().root.get(ContextStore) is kept
assert use_context().root.get(ContextStore) is not kept     # the process as it was
```

As a fixture: `@pytest.fixture(autouse=True)` yielding inside `with fresh_context():`.

## When the store is down

**The store is how replicas keep each other informed, never what a call waits on.** Every read of
the context is this process's memory; the store carries what one replica wrote to the others.
When Redis or Valkey stops answering, every call goes on — each pod works with what it holds, as if
it were alone — and the replicas catch up when it answers again.

| While the store does not answer | What happens |
|---|---|
| Joining it (`store_context_in`, `bus.store_context_in`) | Never fails: the service starts; the seeds wait to be sent |
| The process, the global, a bounded context's level | Read as last read. A write lands here at once and waits, in order, with every write after it |
| An execution's nodes | Stay in this process. A context handed on carries the flow in its headers, and no `x-context-node` the store does not hold |
| An entrance handed a sender's chain | Opens with what the headers carried — the snapshot |
| `bus.cache` | Bypasses the store, computing the value (its own `StoreBypass`) |
| Drafts, pipeline runs, idempotency, leases | **Fail** (`StoreUnavailable`): they promise to persist or to run once, and cannot pretend to |

**When it answers again**, each replica sends what waited, each change on the latest values: two
replicas that wrote `plan` and `maintenance` keep both; the same key goes to whoever sent last. Then
every replica reads what the others wrote.

**One failure stops the asking for a while.** After a failure the store is not asked again for 5
seconds (`OUTAGE_BYPASS`): one call pays the client's timeout, the rest go straight to memory. The
breaker is the process's own — never the [circuit breaker](circuit-breaker.md), whose state lives
in the very store it would guard. The outage is said once, as a warning, and its end once.

Keep the client's own timeouts short, so the call that finds the store down does not wait long —
the client is the project's, and so is its configuration:

```python
# redis.Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5,
#                      retry=Retry(NoBackoff(), 0))  — fail fast, the context falls back by itself
```

## What crosses a process — and what stays

**Each level has an owner, and only the flow crosses.** The global, the process and a bounded
context's level belong to someone and are read where they live, on their owner's store; the other
side of a message reads its own. What crosses — to a queue, another service, the frontend — is the
flow: what the request said at its entrance and what its use cases wrote, from the entrypoint down.

| Level | Owner | Lives on | Crosses a process |
|---|---|---|---|
| global | the organization | the process store | never — every service that set the store reads it there |
| process | the service | the process store | never — each service reads its own |
| bus | the bounded context | its own store | never — each bounded context reads its own |
| entrypoint → application → feature → scope | the flow (the request) | the bounded context's store | always |
| hook (`after_read`, `after_search`) | the hook | this process only | never |

`use_context().flow()` answers what crosses from where it is read; `inject`, `to_client()`, the
envelope of a published message and the remote bus all carry exactly that. A value the flow said
wins on the other side over that side's own levels — a request asked for French, so the receiver
answers in French; a value the flow never said is read from the receiver's own process or bounded
context. A plain mapping handed to `inject({...})` is the caller's word, carried as given.

### Two channels, one flow

| Header | Carries | For |
|---|---|---|
| `x-correlation-id` · `x-causation-id` · `x-execution-id` | the identity | every transport, always |
| `sincpro-context` | the flow's travelling keys, as JSON (lists and one-level mappings too) | the snapshot every receiver reads |
| `baggage` | the flow's scalars, W3C | OpenTelemetry and any other stack |
| `x-context-node` | the id of the node in play, when its bus stores its context | a receiver on the same store: the flow read live |
| `traceparent` · `tracestate` | the trace (`trace_id`, `span_id`, `carrier` never travel in the context) | the trace |

What the receiver reads depends only on the store it reads:

| Receiver | Reads the flow | Written after the hand-off |
|---|---|---|
| the same store as the sender | from the store, by `x-context-node` — live | read |
| a store of its own (one Redis per bounded context) | from `sincpro-context` — the snapshot | not read: only one store sees it |
| only the ids reached it | nothing but the identity | not read |

A bounded context with a Redis of its own is never coupled to another's: the snapshot is enough
for the tenant, the user, the language and what the use cases wrote. Reading what the sender wrote
after the hand-off would mean reading the sender's store — that is the price of separating them.

### How much a header weighs

Only simple values travel: text, numbers, booleans, lists of them, a one-level mapping of those,
and a `Secret` as the value it holds. An object, a connection or a callable stays in its process,
warned about the first time. A typical flow — tenant, user, language, a few values the use cases
wrote — weighs well under 1 KB; gRPC metadata, a W3C baggage and an nginx header buffer take 8 KiB
by default. Past `CONTEXT_BYTES` (4 KB) the context is carried all the same and warned about once,
naming its largest key: **a large value belongs in a store, the context carrying its id** — a draft,
a cached answer, a file are never values of the context.

### Transports

| Transport | What crosses |
|---|---|
| A bus calling another, one process | the whole tree: the callee runs under the caller's node |
| `remote_execution` (HTTP, gRPC) | the caller's flow as one packed header, caused by the calling execution |
| A broker (FastStream), an HTTP or gRPC entrance | `context_propagation.inject` / `context_propagation.extract` |
| The frontend | `use_context().to_client()` out; the `sincpro-context` header back, read by every HTTP entrance |

```python
from sincpro_framework.context import context_propagation


class CommandPublish(DataTransferObject):
    pass


messages = UseFramework("messages-context", log_after_execution=False)


@messages.feature(CommandPublish)
class Publish(Feature):
    def execute(self, dto: CommandPublish) -> None:
        seen.append(context_propagation.inject(use_context()))   # what a message or a request carries


with messages.context({"plan": "pro"}, global_scope=True):      # the bounded context's own level
    with messages.context({"tenant_id": "acme", "tenant_ids": ["acme", "beta"]}):
        messages(CommandPublish())

carried = context_propagation.extract(seen[-1])                 # what the other side opens its flow with
assert carried["tenant_ids"] == ["acme", "beta"] and "causation_id" in carried
assert "plan" not in carried                                    # the receiver reads its own level
```

### The frontend

The browser is one more receiver. `to_client()` gives it the flow — the tenant, the user, the
language of the request, what the use cases wrote — and the frontend sends it back in the
`sincpro-context` header; every HTTP entrance opens its flow with it. What the backend owns stays in
the backend: the process and the bounded context's levels, the caches, the idempotency records, the
leases. A draft reaches the frontend as the answer of a use case (`Edit`, `Get(draft=True)`), never
as context; the frontend's own cache of answers (React Query, SWR) is the frontend's business.

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

| `ContextKey` member | Key | Meaning |
|---|---|---|
| `USER_ID` | `user_id` | who the execution acts for |
| `TENANT_ID` | `tenant_id` | the tenant it acts in |
| `TENANT_IDS` | `tenant_ids` | every tenant it may act across — a list |
| `CORRELATION_ID`, `CAUSATION_ID`, `EXECUTION_ID` | `correlation_id`, `causation_id`, `execution_id` | the identity |

`ContextKey` is a `StrEnum` (`from sincpro_framework.context import ContextKey`): a member is its
text, so `context[ContextKey.USER_ID]` and `context["user_id"]` read the same key. The signals use
their own names: logs, spans and metrics say `tenant` and `user_id`.
