# PRD_21: Execution identity — every execution knows who it is, what caused it and which flow it belongs to

- **Status**: phases 1-4 built, uncommitted — the identity born in the buses, `current_execution()`,
  the generator injectable, events stamped, the identity and the context on every transport, the
  standard keys. Phase 5 (`deadline`) is not built. Additive: no DTO changes, no contract changes,
  nothing a project must do to keep working.
- **Depends on**: the context (`context/`, reshaped by PRD_22 into a tree of nodes), the buses
  (`bus.py`: `FeatureBus`, `ApplicationServiceBus`, `FrameworkBus`), `DomainEvent.caused_by`,
  the entrypoints (REST, JSON-RPC, gRPC), `remote_execution` (the request context as metadata),
  FastStream (queue and wire), observability (`correlation.py`: every context key on every signal).
- **Enables**: PRD_20 (durable workflows), a failure event tied to its flow, audit trails, any
  project-built saga or integration — without the framework imposing one.
- **Philosophy**: an execution and an event are two shapes of one chain. They share the same three
  attributes, the same id format and the same rule, written once. No envelope class, no wrapper,
  no field added to any DTO.

## 1. Problem

What the code does today:

1. **No `correlation_id` unless someone sends one.** Only REST, JSON-RPC and gRPC read
   `x-correlation-id`. A cron, a direct call, a consumer of a message without one, a test — run with
   none.
2. **No identity per execution, no causation between executions.** "This invoice was issued
   because of that sale" cannot be read from any signal.
3. **Events do not inherit the chain.** `record()` and `publish()` leave `correlation_id` and
   `causation_id` empty; only `caused_by()` fills them, by hand. The broker's envelope copies
   `event.correlation_id` — empty — so the chain breaks silently at the first context that forgot.
4. **An ApplicationService's Features are invisible as executions.** It calls `self.feature_bus`
   directly, not through `UseFramework`: no context is opened for them, so nothing could tell them
   apart even if ids existed at the entrance.
5. **The context does not travel the same way everywhere.** In process: all of it.
   `remote_execution`: all of it (pickled). A broker: `correlation_id` and `tenant` only.
6. **The keys disagree.** The docs, `Database(actor=…)`, `AuditedMixin` and the Feature docstring
   teach `"user.id"`; observability reads `"user_id"`. A user set as the docs say never reaches a
   log, a span or GlitchTip.

What it costs: a flow across four machines cannot be followed by one id; a failure cannot be tied
to the request that caused it; every saga, audit or dashboard has to rebuild the chain by hand, and
each one does it differently.

## 2. The model

```
correlation_id = E1 ─────────────────────────────────────────────────────────────────────
E1  CommandConfirmSale    ApplicationService      causation: —     (head: correlation = E1)
 ├─ E2  CommandCheckCredit   Feature (same bus)    causation: E1
 └─ V1  SaleConfirmed        event                 causation: E1
     ├─ E3  IssueInvoice     Feature, billing (machine B)       causation: V1
     │    └─ V2  InvoiceIssued   event                           causation: E3
     └─ E4  ReserveStock     Feature, warehouse (machine C)     causation: V1
```

1. **Every execution of a use case gets an `execution_id`** — the root call, a Feature run by an
   ApplicationService, a Feature run by another context's bus, a Feature run for an event.
2. **`causation_id` is what directly caused it**: the execution that called it, or the event that
   started it. Empty for the head.
3. **`correlation_id` is the flow**: the cause's correlation, else the cause's own id, else its own
   `execution_id` — the rule `caused_by` already has.
4. **An event recorded or published inside an execution takes the same two**, when it does not
   carry them: `causation_id` = the `execution_id` that produced it, `correlation_id` = the
   execution's.
5. **All ids are UUID v7 as 32 hex characters** — the format of every entity and event id. Ordered
   by time, unique across machines, no coordination; an execution's start is its id.

### 2.1 The same attributes as the event

| Attribute | On a `DomainEvent` (today) | In the execution context | Rule |
|---|---|---|---|
| its own id | `id` | `execution_id` | a new UUID v7 per event, per execution |
| `causation_id` | field | key | the id of what directly caused it |
| `correlation_id` | field | key | the cause's correlation, else the cause's id, else its own |
| `tenant`, `user_id` | — | keys (exist) | inherited |

One name differs, on purpose: a context is a mapping anyone writes to, and `id` alone would be
ambiguous there. It is the same value that later appears as a `causation_id` — one id space, no
translation.

What the event has and the execution does not — `name`, `entity_type`, `entity_id`,
`entity_version`, `label`, `created_at` — describes a fact about an entity. An execution is a
request, not a fact; copying them would fill the context with empty keys.

### 2.2 The rule, written once

```python
def chained(cause_id: str | None, cause_correlation: str | None, own_id: str) -> tuple[str | None, str]:
    """(causation_id, correlation_id) of something caused by the cause named."""
    return cause_id, cause_correlation or cause_id or own_id

# cause E1 with correlation C  →  (E1, C)
# cause E1 that carried none   →  (E1, E1)    a cause nobody correlated heads its chain
# no cause, a correlation given →  (None, C)   a request that named its flow
# nothing                       →  (None, own) the head of a new chain
```

`DomainEvent.caused_by` calls it; the bus calls it when it opens an execution; `record` and
`publish` call it when they stamp an event. The event and the execution cannot drift apart.

## 3. Where the identity is born

**In each `execute` of `bus.py`, not at `UseFramework.__call__`** — the only place every execution
passes, the Features of an ApplicationService included (problem 4).

```
UseFramework.__call__(dto)                        opens the request context (as today)
  FrameworkBus.execute
    ApplicationServiceBus.execute    ── opens execution E1 ──┐  a ContextVar, pushed and popped
      ApplicationService.execute                             │
        self.feature_bus.execute(...)  ── opens E2, causation E1
        another_context(dto)           ── UseFramework → opens E3, causation E1
```

- A `ContextVar` holds the execution in progress: a push when `execute` starts, a pop when it ends
  — so nesting, threads (`ThreadContextBus`) and async tasks see the right parent, as the context
  already does.
- The identity is **on every signal**: `execution_context()` — what logs, spans, GlitchTip and
  metrics read — and each bus's log fields add the execution's three ids over whatever the context
  was handed. `self.context` keeps what the execution was handed (the dict a Feature may write to,
  shared by the executions of one call); the execution's own identity is `current_execution()`,
  kept in a `ContextVar` of its own so two Features in two threads never overwrite each other's.
- **`current_execution()`** — a function, callable from a Feature, an interceptor, a hook or a
  component beside the buses — answers what is local and does not travel:

  ```python
  execution = current_execution()     # None outside one
  execution.execution_id · execution.causation_id · execution.correlation_id
  execution.use_case                  # the DTO's registered name
  execution.bus · execution.layer     # "billing" · Layer.FEATURE / Layer.APPLICATION_SERVICE
  execution.started_at                # read off the UUID v7, never stored twice
  ```

  It closes the gap the audit found in interceptors: `(dto, call_next)` knows nothing of where it
  runs; a reusable interceptor for N buses no longer needs each bus handed to it.

## 4. Injectable, at every level

Facilitate, never force — every value can come from outside, and what is given always wins:

| What | How it is given | Default |
|---|---|---|
| `correlation_id` | `bus.context({"correlation_id": …})`, `x-correlation-id`, a message header | inherited, else the head's own id |
| `causation_id` | the same ways — an entrypoint that knows what caused the request | the parent execution, or the event |
| `execution_id` of the root | the same ways — a request id an API gateway already assigned | a new UUID v7 |
| The id generator | `framework.execution_ids(callable)` before the bus is built — ULID, UUID v4, a Snowflake: a `() -> str` | `new_entity_id` (UUID v7) |
| An event's ids | set by the developer, or `caused_by(...)` — stamping only fills what is empty | stamped from the execution |

Nothing is refused: an id in another format is used as given, as text.

## 5. What travels

The context crosses every boundary on the transport's side channel — never in the DTO:

| Transport | Body | Context | Today | After |
|---|---|---|---|---|
| REST, JSON-RPC (in) | body | HTTP headers | reads `x-correlation-id` | reads the identity headers (`x-correlation-id`, `x-causation-id`, `x-execution-id`) |
| gRPC (in) | message | metadata | reads `x-correlation-id` | the same three |
| `remote_execution` HTTP/gRPC | body / message | `x-sp-request-context` / `sp-request-context-bin` | the whole context | unchanged — the identity is in it |
| Broker, publishing (FastStream) | the event's or command's JSON | message headers | the trace and the event's name | + `sincpro-context`: the travelling keys as JSON, caused by the publishing execution; a command's `ce_correlationid` / `ce_causationid` too |
| Broker, consuming | — | restored into `bus.context(...)` | `correlation_id`, `tenant` | `sincpro-context`, then `correlationid` / `causationid` over it |

**What travels is what is simple**: strings, numbers, booleans, ISO 8601 datetimes — `tenant`,
`user_id`, the identity, an idempotency key, the keys a project adds. An object, a connection, a
callable stays in its process, with a warning the first time — never an error.

**Execution records do not travel.** A response, an error, a duration belongs to whoever keeps it —
observability, a workflow's journal, an audit table. The context carries the **ids** that find
them later.

## 6. The keys, made standard

`user_id`, `tenant_id` and `tenant_ids` (a list, for an execution that acts across tenants) are the
keys — constants in `sincpro_framework.context.keys`. `"user.id"` and `"tenant"` keep working: a
context handed to `bus.context(...)` or `carrying(...)` gets the standard key beside the legacy one,
with a warning the first time; the legacy key stays, so nothing that reads it breaks. The signals
keep their names (`tenant`, `user_id`): dashboards do not change. The docs, `Database(actor=…)`,
`AuditedMixin` and the docstrings say `user_id`.

## 6.1 Found while building

1. **A context hosted elsewhere was not handed its caller's context.** A Feature calling a bus that
   `remote_execution` hosts sent only that bus's own context — the tenant and the correlation of the
   request stopped at the first service. It now sends the caller's context, the bus's own over it,
   and the calling execution's chain.
2. **`FastStreamQueue.put` read the trace in the queue's own thread**, where the caller's trace is
   not. Its headers — the trace and the context — are now read where the event is published.

## 7. Phases

| Phase | What | Touches | Breaks nothing because |
|---|---|---|---|
| **1** | `chained()`; `caused_by` uses it; the execution `ContextVar` in `bus.py`; `current_execution()`; ids injectable | `ddd/events`, `bus.py`, `context/`, `use_bus.py` | keys added to the context; DTOs untouched |
| **2** | Events stamped on `record` / `publish` / `RepositoryQueue` when empty | `ddd/entity`, `event_driven` | only empty fields are filled |
| **3** | Entrypoints read the identity headers; the broker writes and restores the context | `entrypoints/*`, `transport/grpc.py`, `event_driven/adapters/faststream`, `entrypoints/faststream` | headers added; missing ones mean "generate" |
| **4** | `user_id` standard, `user.id` read with a warning; key constants | docs, `context`, `orm` docstrings | both keys work |
| **5** | `deadline` — an ISO 8601 moment the whole flow must finish by: travels like the identity; a bus does not start a use case whose deadline passed; `remote_execution` derives its timeout from it | `context`, `bus.py`, `remote_execution` | absent means no deadline |

## 8. What changes for a project, and what does not

- **Does not change**: any DTO, any contract (OpenAPI, gRPC, MCP), any call (`bus(dto)`), any
  Feature.
- **Changes, visibly**: events that went out with `correlation_id = None` now carry one; logs and
  spans carry three more keys; `user.id` warns. A test asserting an empty correlation changes.
- **Documented in**: `docs/core/context-manager.md` (the keys, `current_execution()`, injection),
  `docs/events/README.md` (stamping, events and executions on one chain), the entrypoint and broker
  docs (the headers), the skills `core` and `domain-events`.

## 9. What it solves

| Today | After |
|---|---|
| A flow across machines cannot be followed by one id | one `correlation_id` from the first request to the last event, in logs, spans, GlitchTip and the event table |
| "Why did this happen?" is a guess | the tree of `causation_id`s: which execution or event caused which |
| An ApplicationService's Features are one blur | each Feature its own execution, its parent named |
| Events lose the chain unless every Feature threads it | stamped on the way out |
| A broker drops the context | the same context on every transport |
| A user set as the docs teach never reaches a signal | one key, `user_id` |
| Interceptors cannot tell where they run | `current_execution()` |
| Every saga, audit or dashboard rebuilds the chain by hand | they read it: PRD_20, the failure event, an audit trail stand on this |

## 10. Decisions open

1. **Header names**: `x-correlation-id` exists; `x-causation-id` and `x-execution-id` follow it — or
   W3C `traceparent` alongside (trace and correlation stay distinct: a trace breaks at a broker or a
   cron, a correlation does not).
2. **The generator hook**: `framework.execution_ids(callable)`, or a setting in the framework's own
   configuration.
3. **`user.id`**: a transition with a warning (proposed), or a hard switch.
4. **`deadline`** in this PRD's phase 5, or its own PRD.
