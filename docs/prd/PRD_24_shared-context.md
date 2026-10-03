# PRD_24: The shared context — the same tree, its nodes kept in a store when a bus says so

- **Status**: built, 2026-10-03 — not committed; proved by `tests/context_component/test_shared_context.py`
- **Depends on**: the context (PRD_22 — the tree, the levels, `ContextStore`, `KeyValueContexts`,
  `inject`/`extract`), the execution identity (PRD_21 — `execution_id`, `causation_id`,
  `correlation_id`, UUIDv7), the remote bus (PRD_23), `caching` (`KeyValueStore`, `RedisKeyValue`).
- **Philosophy**: the context API does not change at all — `self.context`, `self.context.get(...)`,
  `use_context()`, `bus.context({...})`, the levels. The only thing that changes is **where** each
  node of the tree is kept: in memory (the default) or in a store. Nothing is shared by default and
  nothing is configured by the environment: a bus that wants it says so, in code.

## 1. The contract

```python
store = KeyValueContexts(RedisKeyValue(redis))          # InMemoryContexts() in tests
sales.context_store(store, ttl=timedelta(hours=24), every=timedelta(seconds=1))
```

| # | Rule |
|---|---|
| 1 | A bus without a store keeps every node in memory — nothing kept, nothing read, nothing shared |
| 2 | Only buses that set a store share; two projects that set the same store (same prefix, default `sincpro:context`) share as well |
| 3 | `context_store(...)` is the one method: it also serves `keep_as` / `restore`, and `root.share` still works |
| 4 | Reading walks up as always — the nearest node wins, wherever the node is kept |
| 5 | A write always lands on a node of this execution; another service's nodes are read-only here |
| 6 | Only an id travels between services; the values live in the store |

## 2. The tree, with a store

```
global                         Level.GLOBAL — key `global`; every service and replica that set the store
 └─ process                    Level.ROOT — key `process:{service_name}`; this service's replicas only
     └─ bus                    local: what a bus publishes; never kept
         └─ entrypoint         ┐
             └─ application    │ one node per execution, each with its own id,
                 └─ feature    │ written through: values + meta (parent id, level, label), with the ttl
                     ├─ hook   │
                     └─ scope  ┘
```

| Level | Kept under | Shared by | Refreshed |
|---|---|---|---|
| `global` | `global` | every service and replica that set the store | by version, at most once per `every` |
| `process` (`Level.ROOT`) | `process:{service_name}` — the bus's observability service name | that service's replicas, never another service | by version, at most once per `every` |
| `bus` | nowhere | — | — |
| `entrypoint` · `application` · `feature` · `hook` · `scope` | the node's id: the execution's `execution_id`; a new id for an entrypoint or a scope | whoever reads that chain (§3) | written through on each write |

- **`Level.GLOBAL`** sits above every process. It exists only once a bus of this process set a store;
  it holds what is system-wide — a maintenance flag, a tenant's parameters. Read and written with
  `use_context().at(Level.GLOBAL)`.
- **The process** is the mechanism `root.share` already uses, keyed by the service.
- **Five concurrent requests are five chains**: each node has its own id — no collision.

## 3. Across services

When an execution hands the context on — the remote bus, a queue message through `inject`, any
`handed_on` — it carries, besides the identity already travelling (`causation_id`,
`correlation_id`), **`sincpro.context_node`**: the id of the node in play — in the context the
remote bus packs, and in its own header, `x-context-node`, on a message.

The receiving bus, when it set a store, opens its entrypoint under the sender's chain, read from the
store:

```
receiver feature → application → entrypoint          its own nodes — writable
 └─ sender feature → application → entrypoint        read-only from here
     └─ (further up, when the sender was itself called by another service)
         └─ the receiver's bus → the receiver's OWN process
             └─ global
```

- **The sender's process never leaks** into the receiver: above the sender's chain sits the
  receiver's own process.
- **The store is the source**: values the message carried that the sender's chain already says are
  not kept on the receiver's entrypoint — so a value the sender writes after handing on is read
  fresh.
- **Writes stay home**: the existing write rules hold — a mapping write lands on the scope of the
  call, `set` on the node in play — and both are always a node of the receiver.
- A receiver without a store opens its entrypoint from what the message carried, in memory: rule 1.

## 4. Reads and writes

| Node | A write | A read |
|---|---|---|
| owned by this execution | written through: each write keeps the node | from memory |
| the sender's nodes | never — the write lands on this execution's node | refreshed by version, at most once per `every` |
| process, global | kept, last write wins | refreshed by version, at most once per `every` |

Never a network call per key: a read costs what it costs in memory; the store is touched by a write
of an owned node and by a refresh whose version moved.

## 5. What stays the same

- Buses without a store: the tree in memory, exactly as PRD_22 built it.
- `keep_as` / `restore` — a snapshot under a key the project chooses — is a separate use case on the
  same store.
- `root.share(store, every=…)` still works.
- Every propagated header (`sincpro-context`, `baggage`, the identity headers) keeps its shape;
  `x-context-node` is one more, written only when a store shares the context.

## 6. Limits and decisions

1. **Last write wins per node.** A node is written by its own execution only, so concurrent writers
   meet only on the process and the global.
2. **The ttl renews on each write** of a node.
3. **The store down → its error surfaces.** Redis unreachable is an error, never an empty context in
   silence.
4. **Codec**: plain JSON by default — it works across projects; a `Secret` travels as its value.
   `TypedCodec` when the projects share code and want types back.
5. **A value under a non-text key** (a settings object, a store) stays in its process — only text
   keys are kept.
6. **Node ids are UUIDv7 execution ids** (PRD_21): ordered by time, unique across replicas.
7. **Opt-in in code only**: no environment variable turns sharing on — a deployment cannot share a
   context its code did not ask to share.

## 7. How mature systems do it

| System | What it does | What we take |
|---|---|---|
| ASP.NET distributed session, Spring Session | the state lives in Redis, keyed by a session id; the id is what the client holds | values in a store, keyed by an id |
| OpenTelemetry Baggage | the values themselves travel in a header, downstream only | propagation on every transport |

This combines both: **only an id travels** (Baggage's propagation, PRD_22's `inject`/`extract`),
**the values live in the store** (the session's model) — so a value written after the hand-off is
seen, and a header never grows with the context.

## 8. Tests

`tests/context_component/test_shared_context.py`:

- nothing set → nothing kept, no `Level.GLOBAL`;
- concurrent executions do not collide — each chain its own ids;
- a value an ApplicationService writes is read by a Feature of another service through a queue
  (`inject` / `extract`) and through the remote bus (gRPC, HTTP);
- a value written after the hand-off is read fresh;
- **only an id travels**: a message stripped to the identity and `x-context-node` still gives the
  receiver every value — the store is the source;
- a receiver that did not set the store reads only what travelled;
- the sender's nodes are read and never written (`TypeError`); what the receiver writes stays on
  its own nodes;
- the process kept under `process:{service_name}`;
- the global shared by every service that set the store;
- the ttl given to the store for every node.
