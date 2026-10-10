# PRD_29: message identity — how every command, query, response and domain event is named, serialized, rebuilt and found

- **Status**: approved by the architect 2026-10-09, simplified (§8); being built on
  `feat/message-identity` in phases P1-P2 (§7), not committed. Gates PRD_28
  (`DomainEvents` reads), whose filters depend on what a name is.
- **Why now**: an audit of the whole framework (§3) found that one DTO has **three
  identities** depending on where it goes, that only domain events have a declared name, and
  that several of the resulting gaps lose or misroute messages **silently**. The name of a
  message is its contract: what a client calls, what a broker routes, what a table stores, what a
  person reads. It cannot depend on how the Python code happens to be laid out.
- **Depends on**: PRD_15 (one failure classification on every wire), PRD_16 (typed gRPC
  contract), PRD_19 (events as entities), PRD_23 (remote bus), PRD_27 (`DEFAULT_*` on the
  entity), PRD_28 (event reads).

## 1. Vocabulary

| Term | What it is | In-process identity | Crosses a process / is stored |
|---|---|---|---|
| **Command** | an intent to change state, a DTO handled by one Feature or ApplicationService | its class | yes: wires, remote bus, queues, workflows |
| **Query** | a question, a DTO handled by one Feature | its class | yes |
| **Response** | what a handler answers | its class | yes |
| **Domain event** | a fact that happened, a `DomainEvent` (an entity) | its class | yes: brokers, queues, event table |
| **Outcome** | the framework's own facts about an execution: `ExecutionCompleted`, `ExecutionFailed` | its class | yes |
| **Failure** | an error as a wire tells it: kind + reason | its class | yes |
| **Aggregate type** | the kind of record an event is about (`entity_type`) | its class | yes: event table, histories |

**A message** is any of the first five. **The class** is how Python dispatches inside one
process. **The name** is how everything else knows it: a client, a broker, another service, a
table, a dashboard, a person. Two identities, kept apart: the class never leaves the process; the
name never depends on the class's module or spelling.

## 2. The rules

1. **Every message has one identity, known at runtime.** A domain event: its `name`, a class
   attribute inherited as today (`name = "billing.invoice.issued"`, default the class name). A
   command, query or response: **`<context>.<ClassName>`**, derived by the bus when the use case
   is registered — nothing to declare.
2. **The runtime registry guarantees it is unique**: two classes with one identity in one bus,
   or two event classes with one `name` in one event table, are refused when the bus is built or
   the table is mapped, naming both.
3. **One serialization**: a message that leaves the process — remote bus, queue, broker — is one
   JSON document carrying its identity (§4). The receiver finds the class by that identity in
   its registry and rebuilds it.
4. **The body describes itself**: a serialized event carries its `name`, so a dead-letter queue,
   a log line or a row read alone says what it is.
5. **One decoding rule everywhere**: fields fit by name — extra ones ignored, missing ones take
   their default, a missing required field is a named `ValidationError` — in every queue,
   broker, remote call and table read.
6. **Nothing that cannot be read breaks what can**: a stored row whose `name` has no class is
   reported, never an exception that hides every other row.
7. **Public names come from the identity**: REST, JSON-RPC, MCP, gRPC, workflow steps, outcomes
   and telemetry name a use case the same way, and adding a bus never renames an existing one.

No aliases, no version field, no upcasters: the framework is new to its projects, and a change
of shape is a change of the class. Name events `context.aggregate.event` so two contexts never
publish the same `name` by accident.

## 3. Where the framework stands today (audit, 2026-10-09)

### 3.1 Commands, queries, responses

| Place | Identifier | Encoding | Rebuilt by |
|---|---|---|---|
| in-process dispatch (`bus.py:95,192`) | class | none | `registry[type(dto)]` |
| `dto_registry` (`ioc.py:94-99,143-150`) | `module.qualname` | — | `map_to_dto_or_event` (`use_bus.py:505-517`) |
| catalog / inspector (`introspection/inspector.py:140`, `entrypoints/catalog.py:73,153`) | `__name__` | JSON Schema | — |
| REST (`entrypoints/fastapi/gateway.py:287-308`) | `/{alias}/{kebab(__name__)}`, operationId `__name__` (alias prefix only on a clash) | JSON | pydantic |
| JSON-RPC (`entrypoints/rpc/wire.py:55-57,146-148`) | `{ns}[.{version}].{snake(__name__ minus Command/Query)}` | JSON | pydantic |
| gRPC (`entrypoints/grpc/naming.py:52-62`) | `{alias}.v{N}/{Alias}Service/{Pascal}` | `google.protobuf.Struct` | `MessageToDict` |
| MCP (`entrypoints/mcp/wire.py:41-47,124-129`) | `snake(__name__)`, alias prefix only on a clash | JSON | pydantic |
| queue `@queue` (`entrypoints/faststream/envelope.py:187-194`) | `ce_type = __name__`; the channel decides | JSON | the class bound to the channel |
| remote bus (`remote_execution/.../execution.py:52-54`, `domain/payload.py:66-173`) | `sp-dto = module.qualname` | pickle of values + module/qualname | `dto_registry[module.qualname]` |
| runtime use cases (`runtime_use_cases/loading.py`, `registry.py`) | `sincpro_runtime.<ctx>.<name>` + class | stored source, `exec` | `map_to_dto_or_event` |
| workflows (`runtime/workflows/entrypoint/workflows.py`) | `"execute": "CommandX"` (`__name__`) | JSON | `catalog[name]` |
| outcomes (`outcomes.py:235,240`) | `use_case = __name__` | JSON | not rebuilt |
| observability (`span_execution.py:82`, `metrics/.../naming.py:24`) | `{bus}/{__name__}`, `{ctx}.{snake(__name__)}` | — | — |
| QueryCaching (`query_caching.py:158-163`) | bus + `module.qualname` + response schema hash | JSON | validate |
| idempotency (`idempotency.py:116,259-265`) | handler and DTO `module.qualname` + full `model_dump` | JSON | `codec.decode` |
| failures (`transport/failures.py:107-114`) | `reason = UPPER_SNAKE(__name__)` of the error | JSON | — |

### 3.2 Domain events and aggregate types

| Place | Identifier | Encoding | Rebuilt by |
|---|---|---|---|
| `DomainEvent.name` (`ddd/events/domain_event.py:72,96-106`) | declared ClassVar, default `__name__` | `as_json` **without** `name` | — |
| `Entity.record` (`entity/entity.py:306`) | `entity_type = type(self).__name__` | — | — |
| bus registry (`ioc.py:94-99`) | `name` | — | `dto_registry[name]` |
| Subscriber (`event_driven/entrypoint/subscriber.py:25-61`) | `name` | `as_json` | the receiver's class, `from_json` (tolerant) |
| BackgroundQueue (`adapters/background_queue.py:35-40,109`) | `name` | `(name, asdict, trace)` | `event_type(**payload)` (**strict**) |
| FastStream (`adapters/faststream/queue.py`, `wire.py:198,411`) | header `sincpro-event` and channel = `name` | `as_json` | `from_json` |
| event table (`templates/events.py`, `event_mapping.py:56-127`) | column `name` = polymorphic identity | envelope columns + `payload` JSON | `from_json` |
| relay (`relay.py:72-134`) | class, walking loaded subclasses | row | `search(cls)` |
| event sourcing (`reading.py:795`, `memory_repository.py:684`) | `entity_type == model.__name__` | — | `rebuilt` |
| `EntityUpdated` (`entity/mixins/tracking.py:75,89`) | `ddd.entity.v1.updated` for every aggregate | `changes` | — |
| outcomes (`outcomes.py:72,95,244`) | `sincpro.execution.v1.completed/failed`, `entity_type="Execution"` | JSON | — |

What a person sees in the table: `id`, `name` (a bare class name unless declared), `entity_type`
(a Python class name), `entity_id`, `entity_version`, `correlation_id`, `causation_id`,
`created_at`, `payload` (JSON), the delivery columns. Every envelope column filters; nothing
inside `payload` does.

## 4. The serialized message

```json
{
  "type": "billing.CommandIssueInvoice",       // the identity: an event's name, a use case's context.Class
  "data": {"customer_id": "c-1", "lines": [...]},
  "correlation_id": "01a9…", "causation_id": "01a8…", "context": {...}
}
```

One JSON document for the remote bus and the queues; the event table keeps the same identity in
its `name` column and the event's own fields in `payload`.

## 5. Gaps, by severity

Severity: **critical** = a message is lost, misrouted or misread silently; **high** = a rename,
a refactor or a deploy breaks a contract; **medium** = inconsistent or lossy, but loud or rare.

| # | Severity | Gap | Proof | Fix | When |
|---|---|---|---|---|---|
| G1 | critical | Two contexts with an event class of the same name exchange events silently: billing's `Paid` reached payroll as payroll's `Paid` with empty fields | `arch_evt_bus.py`: `('payroll','payroll.domain','')` | namespaced names (rule 2); warn on a published/deliverable event with an un-namespaced name now, refuse in 4.0 | 3.x warn, 4.0 refuse |
| G2 | critical | Two classes with one `name` in one event table: the second takes the first's identity and rows read as the wrong class | `arch_evt_db.py`: `Reassigning polymorphic association… 'Posted'`, filter → `['Posted2']` | refuse at `map_events` (rule 3) | 3.x |
| G3 | critical | Two command classes with one `__name__` in one bus: dispatch has both, the catalog keeps one — the other vanishes from REST, RPC, MCP, gRPC, queue and workflows with no warning | `arch_cmd_2.py`: dispatch `A B`, catalog only `sales.a` | refuse at bus build naming both, or key the catalog by message name (rule 3) | 3.x |
| G4 | critical | One stored event whose `name` has no class breaks every read of the context (`AssertionError: No such polymorphic_identity`); the relay never sees it | `arch_evt_db2.py` | skip and report the row (rule 6) | 3.x |
| G5 | critical | BackgroundQueue rebuilds strictly (`event_type(**payload)`): a field added or removed between versions raises `TypeError`, the event is lost, one log line remains | `arch_evt_*`: `unexpected keyword argument 'invoice_id'` | one decoding rule (rule 5): `from_json` as Subscriber and FastStream do | 3.x |
| G6 | high | Renaming an aggregate class orphans its stored events: histories answer nothing, an event-sourced aggregate loses its state | `arch_evt_db2.py`: `renamed aggregate history: 0` | none: a rename is a change of the class; stored rows follow a data migration | — |
| G7 | high | Commands have no declared name; renaming a class changes its REST path, RPC method, MCP tool, gRPC method, `ce_type`, metric, span and every stored workflow at once | §3.1 | one identity `context.Class` for every surface (rules 1, 7) | P2 |
| G8 | high | The remote bus names a DTO by `module.qualname`: a package refactor on one side breaks the other during a rolling deploy; a script registers `__main__.X`; a response without a declared type arrives as a `dict` | `arch_cmd_3.py` | envelope with `type` = name (rule 6); pickle still accepted on input in 3.x, removed in 4.0 | 3.x / 4.0 |
| G9 | high | "Execute by name" means two things: workflows use `__name__`, runtime use cases and `map_to_dto_or_event` use `module.qualname` | `arch_cmd_1.py`: `map_to_dto_or_event('CommandCreate')` → `UnknownDTOToExecute` | one registry keyed by identity | 3.x |
| G10 | high | Adding a bus renames existing contracts: REST operationId and MCP tool get an alias prefix only when a clash appears | `fastapi/gateway.py:305-308`, `mcp/wire.py:124-129` | names come from the message name, namespaced by context, never by clash (rule 7) | 3.x for declared names |
| G11 | high | Idempotency keys include `module.qualname` and the whole `model_dump`: a field added with a default changes the key, so a retry across a deploy runs twice; a stored response whose shape changed fails to decode with no fallback | `arch_cmd_5.py`: `False` | key by message name + the declared idempotency key fields; decode under rule 8 | 3.x |
| G12 | medium | The serialized event does not carry its `name`: a body in a DLQ or a log says nothing about what it is | `as_json` keys | rule 7 | 3.x |
| G13 | medium | Three spellings of one use case: `module.qualname` (registry, remote, idempotency), `__name__` (catalog, wires, workflows, outcomes, telemetry), class (dispatch) | §3.1 | rules 1 and 9 | 3.x |
| G14 | medium | JSON-RPC tells a failure by the error's class (`HSM_DOWN`) where REST, gRPC and the queue say `UNAVAILABLE` | `arch_cmd_4.py` | RPC uses `failure_reason` like the other wires (PRD_15) | 3.x |
| G15 | medium | `EntityUpdated` is one name for every aggregate of every context; a subscribed bus receives all of them | `tracking.py:75,89` | its `subject` carries the aggregate type; namespaced per context on request | 3.x |
| G16 | medium | No versioning: no `version`, no upcasters; a changed shape silently defaults or fails | `docs/persistence/use-cases.md:162` | out of scope (§8) | — |
| G17 | medium | A filter on a payload field is dropped as `unknown_field` on SQL but applied in memory: the two stores answer differently | `arch_evt_db2.py` | parity: refuse/report the same in memory; payload filters on JSONB are a 4.0 study | 3.x parity, 4.0 study |
| G18 | medium | gRPC carries `Struct`: integers above 2⁵³ change, bytes and Decimal do not travel | `grpc/proto.py:7-10` | PRD_16 (typed contract) | as PRD_16 |
| G19 | medium | Legacy OpenAPI publishes the first of two homonymous schemas | `rest/openapi.py:75-82` | covered by G3 | 3.x |
| G20 | low | A blank or whitespace event `name` is accepted | `domain_event.py` | refuse at declaration | 3.x |

What already holds: in-process dispatch by class; the remote pickle's class allow-list; QueryCaching
keyed by the response schema hash (only ever a miss); remote payloads fit by field; auth decoupled
from DTO names.

## 6. How a message is declared

```python
class InvoiceIssued(BillingEvent):          # an event: its name, as today
    name = "billing.invoice.issued"
    invoice_id: str

class CommandIssueInvoice(DataTransferObject):  # a command: nothing — it is billing.CommandIssueInvoice
    customer_id: str
```

`identity_of(dto_class, bus)` answers the identity wherever the framework needs it; nothing
reads `__name__` or `module.qualname` on its own.

## 7. Plan

| Phase | Delivers |
|---|---|
| **P1 — stop silent loss** | G2, G3, G4, G5, G12, G14, G19, G20 |
| **P2 — one identity and one serialization** | the registry keyed by identity (events by `name`, use cases by `context.Class`), the remote bus and the queues on the JSON message instead of pickle and `module.qualname`, workflows/outcomes/telemetry naming a use case by its identity (G7, G8, G9, G10, G11, G13, G15), paired SQL/memory behaviour for payload filters (G17) |
| **then** | PRD_28 reads on events, by `name` |

Out of scope: G1's cross-process clash of un-namespaced names is a naming rule (§2), G16
versions and G18 gRPC types (PRD_16) are not this work.

## 8. Decisions (architect, 2026-10-09)

- Identity derived at runtime, never declared for use cases; events keep `name`.
- No aliases, no versions, no upcasters, no decorator: the framework is new to its projects.
- The runtime registry refuses duplicates when the bus is built.
- One JSON serialization replaces the remote pickle.

## 9. Research (2026-10)

- **Axon 5**: every message (command, event, query) carries a `MessageType` — a qualified name
  (`namespace.name`) plus a version — declared with `@Command`/`@Event`/`@Query`; handlers resolve
  by type, not class. docs.axoniq.io/axon-framework-reference/5.1 (anatomy of a message).
- **Wolverine**: `[MessageIdentity("alias", Version=2)]` on any message; the version travels in
  the content type; one handler per version or `IForwardsTo<T>`. wolverinefx.net/guide/messages.
- **KurrentDB**: `type` is a mandatory free string; "we recommend against [the class name] as it
  couples the storage to the type". docs.kurrent.io (appending events).
- **Marten**: stores an alias (`type`) and the .NET type; `MapEventType<T>("old")` for renames,
  `Events.Upcast<Old,New>` for versions. martendb.io/events/versioning.
- **CloudEvents**: `id`, `source`, `specversion`, `type` required; reverse-DNS `type`; a
  compatible change keeps the type, an incompatible one changes it. github.com/cloudevents/spec.
- **NServiceBus / MassTransit**: still tied to the .NET type (`EnclosedMessageTypes`,
  `urn:message:Namespace:Type`, overridable with `[MessageUrn]`); a breaking change is a new type.
- **Temporal, Celery, gRPC, MCP**: declared names (`@workflow.defn(name=…)`, `name=`, full method
  names, tool names unique per server); protobuf evolves by field number, never reused.
- **Kafka + Schema Registry**: `TopicNameStrategy` by default, `RecordNameStrategy` for several
  types per topic; `BACKWARD` compatibility by default.
