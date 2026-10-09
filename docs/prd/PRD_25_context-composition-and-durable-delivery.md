# PRD_25: Context composition, public contracts and durable delivery

- **Status**: proposed, 2026-10-08. Assessment and acceptance criteria only; no new runtime API is implemented by this document.
- **Consumer evidence**: the current `sincpro_forge` monolith, with one PostgreSQL `Database`, four context-owned migration chains and seven event sources.
- **Builds on**: [declared exposure](PRD_14_declared-exposure.md), [transactional persistence](PRD_17_transactional-persistence.md), [events as entities](PRD_19_events-as-entities.md) and [context composition](../entrypoints/integration.md).
- **Scope**: composition, contract discovery, documentation and delivery guarantees. No new transport, broker requirement, global migration chain or universal CRUD API.

## 1. Recommendation

Keep the bus as the application boundary and `Gateway` as the owner of resolved transport exposure.
Compose context-owned capabilities at the process root. Add only the operational information
that the existing catalog cannot infer: event sources, consumer identity, delivery policy,
worker lifecycle and migration dependencies.

One database does not imply one context, one metadata object, one event table or one migration
chain. Durable delivery is useful in a monolith too: a database commit cannot atomically commit
an external notification, agent request or repository operation.

## 2. What already works

| Concern | Existing mechanism | Keep it |
| --- | --- | --- |
| Application execution | `UseFramework`, `Feature`, `ApplicationService`, DTO-based dispatch | One bus per context; no second use-case registry in entrypoints |
| Writes | `Repository.save`, aggregate operations, owned relations, version checks | Semantic Commands call domain operations, then save |
| Transactions | `Repository.context`; repositories with the same `Database` join the active unit | Transaction scope must remain explicit |
| Reads | `Criteria`, `Specification`, paginated responses | Preserve scope, masks and pagination metadata |
| Exposure | `Catalog`, bindings, `Gateway.surface`, `manifest` and validation | Derive the published surface from existing handlers |
| Event persistence | `record`, `event_table`, `map_events`, `DeliverableEventMixin` | Save the aggregate and its mapped events together |
| Delivery | `EventRelay`, failure policies and `Crons.run_relay` | Reuse the native outbox, not a second event-entry model |
| Schema lifecycle | `ContextMigrations`, stores and dependency-aware composition | Context ownership survives a shared database |

Gateway declarations are intentionally explicit. `DECLARED` publishes bindings for its wire;
`CATALOG` is a deliberate broader mode. `internal`, filters, groups, bindings and overrides
already express exposure policy. A new descriptor must not override these decisions by merely
finding a handler in a module.

## 3. Entrypoints and public contracts

Three concepts currently share vocabulary but have different responsibilities:

1. **A public Python contract**: the DTOs and domain events a context allows consumers to import.
2. **A transport entrypoint**: a gateway or adapter translating input into a bus call.
3. **A process composition root**: assembling already configured contexts, workers and migrations.

The existing transport arrangement is sound: services register handlers, gateways read the
catalog, and routes call the bus rather than `execute`. Root entrypoints may assemble gateways
and lifecycle components, but must not register substitute Features, own business workflows or
construct a second bus for the same context.

### The public facade decision is not settled

Forge exposes domain events through context `entrypoints/events.py`. Definitions stay in domain,
but using this facade from infrastructure or services conflicts with the current
`entrypoints-are-outermost` rule. That rule permits only entrypoints to import entrypoints.

Choose and document one policy before moving imports:

- Keep entrypoints strictly outermost; use a separate, explicitly public contract facade for
  in-process consumers, while entrypoints import it to expose transports.
- Or formally distinguish passive contract facades from executable entrypoints, and update the
  architecture checker and examples to permit only the passive facade imports.

The first policy needs no exception to dependency direction. The second preserves Forge's
chosen facade location but requires an enforceable distinction; a blanket exemption for
`entrypoints/` would admit gateways and worker internals into services.

In either policy, shared contract definitions belong to the producing context's domain. A
local-only DTO may remain beside its handler. Consumers must not need to import a handler class
to obtain a shared contract, and a facade must not initialize adapters or start workers.

### What can be inferred

| Derivable from code | Must be declared or verified |
| --- | --- |
| Registered DTOs, handlers and response annotations | Which contracts are public and stable |
| Query versus Command, declared bindings and groups | External exposure, authorization and tenancy policy |
| Resolved gateway operations and manifests | Retry, ordering and duplicate-effect tolerance |
| Event names known by a subscriber's buses | Required consumers and their durable identities |
| Migration stores and declared dependencies | Which processes run and how readiness is reported |

A type annotation is descriptive: `bus(dto, Response)` is not response validation. Likewise,
an ApplicationService composes execution, not an implicit distributed transaction. Neither
claim should be inferred from naming or generated documentation.

## 4. Composition proposal

Evaluate a small, typed, opt-in context descriptor rather than an application-wide registry
that duplicates `UseFramework`. Its minimum responsibilities would be:

- Reference an already configured bus and its stable composition alias.
- Identify public contracts without redefining their DTOs or schemas.
- Declare event sources and the database/repository they use.
- Reference context-owned `Crons` and `ContextMigrations`, including existing dependencies.
- Describe required consumers and operational delivery policy.

These are responsibilities to evaluate, not a proposed callable signature. First prove the
shape with Forge and another consumer. The root should aggregate declarations; context
infrastructure remains responsible for mappings, dependencies and adapter selection.

Build order remains explicit: configure buses and mappings, import handler registrations,
attach guards/interceptors, resolve gateways, then start the selected processes. Discovery
must not depend on importing every Python module or executing live adapters.

HTTP, bus-only operation, cron and outbox delivery should be independently selectable. Starting
a gateway must not silently start delivery workers, and defining a context must not require
a transport extra.

## 5. Durable-delivery assessment

These observations describe the current `SyncQueue` / `Subscriber` / `EventRelay` path, not every
broker adapter or an assertion that the framework has no general idempotency primitives.

### [side-effect] Partial fan-out repeats successful consumers

Evidence: `event_driven/services/subscriber.py`, `Subscriber.handle`, calls buses in order;
`entrypoint/relay.py`, `EventRelay._delivered`, records one delivery outcome for the event.
If consumer A succeeds and B raises, the next attempt invokes A again; later consumers were not run.

Requirement: stable consumer identities and durable receipts keyed by `(event_id, consumer_id)`.
For database-only consumers, their business writes and receipt must commit atomically. Evaluate
existing idempotency facilities before introducing another subsystem, but do not equate a
key-value execution marker with an atomic SQL consumer receipt.

### [side-effect] External I/O occurs while claim locks are held

Evidence: `EventRelay.run_once` wraps claim, synchronous publication and outcome persistence in
one repository context. Forge's `ReportIssueProgress` publishes/updates external messages on
that path. Its repository shares the same `Database` and can join the relay transaction.

Keep the current atomic path available for short database-only reactions. Evaluate a separate
lease-based claim/process/ack mode for external I/O. Such a mode needs lease expiry, ownership
checks, renewal and stale-worker behavior; merely releasing the lock is not sufficient.

No mode makes an external side effect transactional with SQL. Lost replies or a crash before
acknowledgment require provider idempotency keys or application-level reconciliation.

### [side-effect] Retry ordering is not a cross-pass guarantee

Evidence: `_carried_out` keeps its held entities in a local set; `_due` selects due events.
A predecessor waiting for backoff can be absent from another pass while its successor is due.

Requirement: make ordering a declared policy. If ordered delivery is requested, an unresolved
predecessor must block successors across retries and replicas; define what parking/replay means.
Do not advertise strict ordering based only on sorting event ids within one batch.

### [ai-context] No listeners looks like successful delivery

Evidence: `Subscriber.handle` returns an empty list when no bus knows the event name, and the
relay marks a non-raising publication delivered.

Requirement: explicit ignore/warn/refuse policies, plus validation of required subscribers at
composition time. A recorded audit event is not automatically an externally deliverable contract.

### [duplicate] Forge has two independent publisher compositions

Evidence in the consumer: `domains/common/infrastructure/events.py` still constructs `PUBLISHER`
from `BUSES`; root `entrypoints/events.py` constructs its own publisher and lists event sources.
No other current Forge Python file references that `PUBLISHER` symbol in the inspected tree.

Consumer correction: remove obsolete wiring after confirming intended ownership. Framework
improvement: make the selected delivery topology inspectable without another hand-maintained list.

Operational gap: no worker-start reference was found in Forge's inspected Makefile, deployment
definitions or package scripts. This does not prove nobody starts it manually; the runnable
entrypoint and its deployment lifecycle need an explicit documented connection.

## 6. Writes: convenience without universal CRUD

`Repository.save(aggregate)` already supplies the persistence primitive. Do not expose an
automatic `CommandSaveEntity` that accepts arbitrary fields and bypasses domain operations,
authorization, tenant scope, semantic events or aggregate ownership.

An optional scaffolder could generate a typed Command/Response and Feature skeleton for create
or update. Its extension points must preserve domain validation and explicit transaction scope;
it must not publish the generated handler on a transport by default.

Also evaluate a supported typed JSON/value-object mapping recipe against Forge's `StructuredJson`
adapter. This is a candidate simplification, not evidence that every JSON mapping capability is
missing from the framework.

## 7. Documentation and inference corrections

| Priority | Correction | Acceptance criterion |
| --- | --- | --- |
| P0 | Settle passive public facades versus outermost entrypoints | Guides, architecture checks and one bundled-context example agree on allowed imports |
| P0 | Correct `docs/shapes.md`'s implicit-write example | Default `Writes.SAVED` uses `unit.save`; any `Writes.CHANGED` example is explicit about hooks/cascades |
| P0 | Separate database topology from delivery needs | A one-database monolith example uses a native outbox without globalizing context migrations |
| P1 | Distinguish execution composition from transaction composition | ApplicationService guidance names when a shared UoW exists and where it does not |
| P1 | Bring entrypoint navigation up to current Gateway vocabulary | Index and skills distinguish current gateways from legacy helpers and explain declared exposure |
| P1 | Document public contracts and worker lifecycle | A reader can locate DTOs/events, discover published operations and identify required processes |
| P2 | Automate structural documentation from existing introspection | Catalog/surface manifests drive tables; explanatory policy remains hand-authored |

Generated API schemas and gateway manifests describe signatures and resolved exposure. They do
not establish delivery guarantees or business intent. Keep those decisions authored and linked
to the generated evidence, rather than introducing a competing YAML schema catalog.

Documentation updates should follow the owning change: contract definition, registration,
exposure, operational declaration, then guides/examples/skills. Verify the installed consumer
artifact separately from the framework checkout or agent-skill revision.

## 8. Verification and next iterations

Assessment evidence is source and guide inspection; failure-injection scenarios below are
acceptance criteria, not tests executed by this document. No Forge old-model tests, external
providers, deployments or database migrations were run for this assessment.

1. Align the facade/layer rules and correct documentation defaults before introducing a descriptor.
2. Add composition checks: bus-only boot, handler loading before gateway build, no missing required
   consumer, no unexpected public binding, and independent context migration ownership.
3. Prove consumer receipts: A succeeds/B fails; replay skips A and eventually executes later consumers.
4. Prove SQL atomicity: handler rollback never leaves a successful receipt; distinct `Database`
   instances are not presented as one transaction.
5. Prove external delivery limits: crash after publish/before acknowledgment, lost reply, lease expiry,
   stale worker and two replicas. Duplicate prevention must identify the responsible mechanism.
6. Prove requested ordering across backoff, replicas, parking and replay.
7. Expose pending count, oldest pending age, attempts, parked events, consumer outcomes and worker
   readiness; replay stays an explicit operation.

The governing distinction is **recording a fact, delivering it and acknowledging each consumer
are different contracts**. Transactional Outbox and Idempotent Consumer address those contracts;
the Composition Root and hexagonal entrypoints make them understandable without teaching the
domain about the transport. No broker or universal write Command is required to obtain that separation.
