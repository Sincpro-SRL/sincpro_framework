# PRD_23: The remote bus — a bounded context hosted elsewhere is a reference to a bus, typed as one

- **Status**: built, 2026-10-02 — R1–R4 (§6), proved by the contract suite
  (`tests/remote_execution/test_contract.py`) on local, gRPC and HTTP. Not committed.
- **Depends on**: `remote_execution` (transports, payload, Open Hosts), the context (PRD_22), the
  execution identity (PRD_21), the outcomes (`sincpro_framework.bus_pipeline.outcomes`), `transport.failures`.
- **Enables**: PRD_20 (distributed transactions by signals) and the release that unblocks the
  products built on several services.
- **Philosophy**: a bus receives commands and returns answers — nothing else. Whether it runs here
  or on another service is configuration, never code. Remote is not local, though: it can be slow,
  down, or leave the caller not knowing whether it ran — so the face is the same, and the failures
  that only a network has are named, never hidden.

## 1. The contract

**Rule 0.** `UseFramework` is the one type a bounded context has. Configured as hosted elsewhere
(`context_map`, `SINCPRO_CONTEXT_MAP`, `hosted_by`), it is a **reference**: a client of the
protocol with the face of the bus — it builds nothing, emits nothing, and forwards every call. The
code that calls it, injects it or types it does not change. What the use case emits (outcomes,
events, spans) the real bus emits, where it runs, as its own configuration says.

```python
# the package both services install: billing's DTOs and handlers, as today
# service A — configuration only
#   SINCPRO_CONTEXT_MAP="billing=grpc://server-b:50051?timeout=5"
sales.add_dependency("billing", billing)

class ConfirmSale(ApplicationService):
    billing: UseFramework
    def execute(self, dto: CommandConfirmSale) -> ResponseConfirmSale:
        invoice = self.billing(CommandIssueInvoice(order_id=dto.order_id), ResponseIssueInvoice)
        invoice.number                       # a ResponseIssueInvoice — local, gRPC or HTTP alike

# service B
billing.serve("0.0.0.0:50051")
```

| # | Rule | Proved by |
|---|---|---|
| 1 | `bus(command, Response)` answers **an instance of `Response`** — local, gRPC and HTTP alike | the contract suite (§5), one set of cases run on the three |
| 2 | Without `Response`, it answers **the type the handler declares**; a DTO never arrives as a `dict` | nested DTOs, lists of DTOs, `Optional`, unions, a handler declaring `Any` |
| 3 | **No version validation**: each team deploys its bounded context at its own pace, and two deployments talk as long as the DTO fits — a field one side does not know is ignored, a field with a default may be absent. The only errors: the DTO does not fit (`DTODoesNotFit`, naming the fields), the host does not answer that DTO (`UnknownDTOToExecute`), the host does not answer (`ContextUnavailable`) | a newer caller with an extra field (works), an older one without a field that has a default (works), a required field missing, a renamed DTO |
| 4 | A reference is **never built**: no Feature instantiated, no dependency resolved — by any path | one case per path (§3.1), with a dependency that raises when touched |
| 5 | It is **injected as a bus**: `add_dependency("billing", billing)`, `self.billing(cmd, Resp)`, the same types for the IDE and pyright | an ApplicationService calling a reference, sync and async |
| 6 | **The context travels as it is** — both ends run Python: typed values, `Secret`, identity, tenant. The framework filters nothing: what goes in the context, secrets included, is the project's responsibility | an Enum, a DTO, a `Decimal`, a `SecretStr` and a `Secret` in the context |
| 7 | **An error arrives as itself** — its class and its attributes (`ContextRequired.missing`); `ContextFailed` only when its class does not exist where it is raised | exceptions with an `__init__` of their own, a pydantic `ValidationError` |
| 8 | **What only a network can do is named**: did not run (`UNAVAILABLE`, retry), may have run (`UNKNOWN_OUTCOME`, verify before retrying or undoing) | a host that is down, a deadline passed after the call was sent |

## 2. Where we stand

| Rule | Measured 2026-10-02, before | Built |
|---|---|---|
| 1 | ✅ | ✅ |
| 2 | ⚠️ a handler declaring `Any` answered DTOs as `dict`s | ✅ each DTO travels with the name of its class |
| 3 | ⚠️ a DTO that did not fit arrived as `ContextFailed` | ✅ `DTODoesNotFit` naming the fields, both ways |
| 4 | ❌ five paths built the reference | ✅ `build_root_bus()` builds nothing for a reference; every path reads registrations |
| 5 | ⚠️ async and events built it | ✅ |
| 6 | ❌ a `Secret` failed the call; a model arrived as a `dict` | ✅ each value packed on its own, typed |
| 7 | ❌ only `(module, class, message)` travelled | ✅ `args` and attributes, rebuilt without `__init__` |
| 8 | ❌ timeouts and unavailability were `INTERNAL` | ✅ `UNAVAILABLE`, `UNKNOWN_OUTCOME` on every wire |

What already held and stays: the call is synchronous and blocks until answered; payloads of any size
travel in 1 MiB chunks (96 MiB tested byte-exact); `bytes`, `Decimal`, dates, `UUID` and enum members
travel as themselves; one channel per address, shared across threads; the context and the identity
chain (`handed_on`) cross; who the call acts for crosses as credentials, never as a context claim.

## 3. Findings

### 3.1 Building a reference

Building a bus instantiates every Feature and ApplicationService and resolves the dependencies
injected into them — a `providers.Singleton(Engine)` is created then. On a service where the context
is hosted elsewhere, its dependencies are not configured, and the build fails:

```python
billing.add_dependency("engine", providers.Singleton(Engine))   # Engine needs DB_URL
billing.hosted_by("grpc://server-b:50051")
billing.build_root_bus()          # RuntimeError: DB_URL not configured here
```

`bus(dto)` forwards before building (`use_bus.py` `__call__`). These paths build first:

| Path | Where | Reached by |
|---|---|---|
| `get_async_bus()` | `use_bus.py` `_built_bus()` | every async call |
| `Subscriber` | `event_driven/entrypoint/subscriber.py` (`bus.get_async_bus()`) | every event a reference listens to |
| `dto_registry`, `map_to_dto_or_event` | `use_bus.py` | queue consumers, the host itself |
| `with_trace()`, `with_parent_trace()` | `use_bus.py` | tracing a call |
| catalog, introspection, runtime use cases, testing | `entrypoints/catalog.py`, `introspection/operations.py`, `runtime_use_cases/registry.py`, `testing/dependencies.py` | exposing or listing a context; `fresh()` |

Everything those paths read — the DTOs a bus answers, its handlers, their declared responses — is
known at registration, without building.

### 3.2 How each thing travels

| What | How | Lost on the way |
|---|---|---|
| The DTO | dumped by its class (pydantic, python mode), pickled through an allow-list, rebuilt by its class on the host, found by `module.qualname` | nothing, with the same code on both ends |
| The response | dumped by the type of the value the handler returned; rebuilt by the caller with the type given, else the one the handler declares | the type, when the caller has none to rebuild with (`Any`) |
| The context | `handed_on` (everything but this execution's id), pickled through the same allow-list | a `Secret` fails the call; a model becomes a `dict` |
| An error | gRPC trailing metadata / HTTP body: module, class, message; rebuilt as `cls(message)` | its attributes; any class whose `__init__` takes more than a message |
| Identity, trace, credentials | metadata / headers | nothing |

### 3.3 Failures only a network has

- `ContextTimeout` (gRPC `DEADLINE_EXCEEDED`, HTTP read timeout): the host may have run it.
- `ContextUnavailable` (gRPC `UNAVAILABLE` / `UNIMPLEMENTED` / `NOT_FOUND`, HTTP connect error): it
  did not run — except gRPC `UNAVAILABLE` after the stream was sent (the connection dropped
  mid-call), which may have.
- Neither carries a `failure_kind`: both are `INTERNAL`, so a caller cannot tell "retry" from
  "verify first", and a wire answers 500 for both.
- The deadline is fixed per address (`?timeout=`): A → B → D waits each hop's own.
- The gRPC channel is `insecure_channel` — an internal network only.

### 3.4 How mature systems draw the line

| System | What it teaches |
|---|---|
| gRPC stubs | the generated client has the service's face; deadlines propagate; status codes separate "not reached" from "deadline exceeded" |
| Dapr service invocation | the app id resolves to an address by configuration; the caller's code names only the app |
| NServiceBus / MassTransit | request/response share a contracts assembly; a fault carries the exception's type and details |
| Temporal | application errors travel with their type and details; "non-retryable" is a property of the error |
| Akka, *A Note on Distributed Computing* (Waldo et al., 1994) | location transparency for the face, never for failure: partial failure and latency are named |

Our choice follows all five: the face is the bus's (Dapr, gRPC stubs), contracts are the shared
package (NServiceBus), errors travel with type and details (Temporal, NServiceBus), and the failures
that only a network has get names of their own (Waldo).

## 4. Design

### 4.1 A reference is never built

- A bus hosted elsewhere and not run by this process is a reference (`is_reference`):
  `build_root_bus()` builds nothing, and every path of §3.1 reads what it needs from registrations —
  `dto_registry`, `feature_handlers()`, `app_service_handlers()`, their declared responses.
- `get_async_bus()` builds nothing either: each call runs the bus's own call, which builds a local
  bus on first use and forwards a reference.
- A `Subscriber` hands an event to a reference by forwarding it — the host runs it.
- The catalog and introspection describe a reference from its classes (no instance, no
  interceptor — they run where it is hosted) and forward its calls; `is_ready` is true for it.
- `override_dependencies` on a reference refuses, saying it has nothing built to replace.

### 4.2 Responses always as the DTO

Every DTO, dataclass and pydantic secret in a payload is written as its values **and the name of its
class** (`_Pickler.reducer_override`), wherever it sits — alone, in a list, in a dict. The caller
rebuilds:

1. as the type it gave (`bus(dto, Response)`) — the values, fitted by that type;
2. else as the type the handler declares, when it is one (not `Any`);
3. else each DTO as the class the host named, when this process imported it;
4. final: its values — a class this process never imported.

A response that does not fit the type is `DTODoesNotFit` naming the fields. One that fits is
answered, whatever version each side runs.

### 4.3 Errors as themselves

The host sends the error's class, its message, its `args` and its own attributes (values through the
payload's allow-list; dunder attributes and what cannot be written stay; past 4 KiB only the
message travels). The caller rebuilds the class without calling its `__init__`, sets its `args` and
its attributes: `ContextRequired`, `SignerDown(signer, attempts)`, a `DomainError` with fields —
each arrives as itself. `ContextFailed` remains for a class this process never imported. A DTO
that does not fit on the host arrives as `DTODoesNotFit` (kind `INVALID`) naming the fields.

### 4.4 The context travels whole

Every value of the context travels — the framework filters nothing: what a project puts in its
context is its decision, security included. Each value is packed on its own, so a `Secret` travels
as a `Secret`, a DTO as its class, and one value that cannot be written (a lock, a connection) stays
behind with a warning instead of failing the call.

### 4.5 Failures with names

- `ContextUnavailable` declares `UNAVAILABLE` — it did not run, a retry may succeed: nobody
  answered, the request was not written whole (HTTP), or the host had not accepted the call (gRPC:
  the host answers `sp-accepted` before it reads the DTO).
- `FailureKind.UNKNOWN_OUTCOME`, declared by `ContextOutcomeUnknown` — it may have run: the
  connection lost after the call was sent or accepted — and by `ContextTimeout`, its subclass. Not in
  `RETRYABLE`: a caller verifies (or retries with an idempotency key) before doing it again or
  undoing it. Every wire maps it: REST 504, JSON-RPC `-32024`, gRPC `DEADLINE_EXCEEDED`.

### 4.6 Local unless this process says otherwise

A context runs here — built, as it always was — unless **this process's** configuration names it
elsewhere. Each deployment names only what it reaches in another service:

```
service A (sales):     SINCPRO_CONTEXT_MAP="billing=grpc://billing-svc:50051"
service B (billing):   nothing — billing is local: its server, its workers, its crons, its startup
```

- Nothing configured, nothing changes: every existing project keeps building every context here.
- `SINCPRO_CONTEXT_MAP` is read from the environment whether or not the project's own conf file
  declares `$ENV:SINCPRO_CONTEXT_MAP` — a deployment that sets it is never ignored.
- A reference says so once when it is created (`billing is hosted at grpc://…: this bus forwards
  every call`), so a process's start shows everything it reaches elsewhere.
- A safety net, not a mechanism: what this process serves (`serve`, `open_host`,
  `open_host_routes` — each calls `run_here()`) runs here even when its own map names it, so a
  service never calls itself.
- In code, `hosted_by(url | HostedAt)` points a bus without any configuration — tests, scripts, a
  hand-wired composition root. **The last one applied wins**: the configuration at creation
  (`SINCPRO_CONTEXT_MAP` over the conf file, per context), then `hosted_by` replacing it — so the
  environment can no longer move an address fixed in code — then `run_here()` / serving, over
  both. Production services leave the address to their configuration.

### 4.6.1 A typed address

The address is written as a URL — the one string an environment variable holds — and read into a
typed, immutable value, as SQLAlchemy reads a database URL into its `URL` and Spring binds a
client's `url` and timeouts into typed properties that fail at startup:

- `Wire` (`StrEnum`: `GRPC`, `HTTP`, `HTTPS`) — the scheme; `transport_for` matches on it.
- `HostedAt(wire, address, timeout)` — `HostedAt.parse(url)` or built in code; checked either way.
- `HostedContext(context, at)` — one `context_map:` entry; `extra="forbid"`, so a misspelt key is
  an error, not an entry silently ignored.
- They live in `sincpro_framework.common.transport.addresses`: the settings declare them and remote
  execution reaches them, so the module imports neither.
- The conf file is validated when it is loaded, each error at its path (`context_map.0.at`); the
  environment when the first bus is created, naming `SINCPRO_CONTEXT_MAP`. Every `InvalidAddress`
  says what was written and what to write. The timeout stays in the URL (`?timeout=5`), as
  `?connect_timeout=` does in a database URL: it is the one option, the same on both wires.

### 4.7 The lifecycle — when a bus is a reference, and when it is built

1. **Created** — `UseFramework("billing")` reads this process's map (`configured_host`): nothing
   named, a local bus; named, a reference, said in the logs. Registrations (`@feature`,
   `add_dependency`) are kept, never resolved.
2. **Decided** — at every call, never cached: `is_reference = hosted_at is not None and not
   run_here`. `hosted_by(...)` and `run_here()` change it.
3. **Served** — `serve`, `open_host`, `open_host_routes` call `run_here()` first, then build: the
   host's bus is a real, local one.
4. **Built** — `build_root_bus()` is idempotent and never fails for a reference:

| The bus | `build_root_bus()` | `was_initialized` | `is_ready` |
|---|---|---|---|
| local | builds once, under a lock — a second thread waits for that first build only | `True` | `True` |
| reference | returns at once: no Feature, no dependency, no error, no warning | `False` | `True` |

   A local bus builds on its first execution when nobody built it before. A reference never does.
   Every path that builds "just in case" (catalog, gateway, introspection, `with_trace`) calls
   `build_root_bus()` unconditionally and works on both.
5. **Executed** — `bus(dto)` / `get_async_bus()`: a reference forwards; a local bus executes.

### 4.8 After the release

- A deadline in the context (PRD_21 phase 5): the time left travels, and each hop waits the shorter of
  its own timeout and what is left.
- `grpcs://` with mTLS between services.

## 5. The contract suite

One suite, the cases of §1, parametrized over `local`, `grpc` and `http` — what passes on one passes
on the three:

- responses: DTO, nested DTO, `list[DTO]`, `Optional`, union, `None`, plain values, `Any` returning a
  DTO, with and without `Response`;
- errors: `DomainError`, a project exception, a builtin, an `__init__` of its own, `ContextRequired`,
  a `ValidationError`, a class the caller never imported;
- context: `Secret`, Enum, `Decimal`, a DTO, the identity chain, a `correlation_id` written mid-flow;
- versions: a caller with an extra field, a host with a new field that has a default — both answered;
  a required field missing — the error naming it;
- a reference never built: sync, async, a `Subscriber`, `dto_registry`, `with_trace`, the catalog —
  with a dependency that raises when touched;
- injection: an ApplicationService on another bus calling the reference, sync and async;
- the network: host down → `UNAVAILABLE`; deadline passed → `UNKNOWN_OUTCOME`.

## 6. Phases

| Phase | What | Rules |
|---|---|---|
| **R1 — a reference** | never built by any path; async, `Subscriber`, catalog and registry forward; the contract suite's first cases | 4, 5 |
| **R2 — what travels** | responses named and rebuilt; errors with their attributes; the whole context, nothing filtered, on every way out; what does not fit named | 1, 2, 3, 6, 7 |
| **R3 — failures with names** | `UNAVAILABLE`, `UNKNOWN_OUTCOME`, mapped on every wire | 8 |
| **R4 — where it runs** | local unless this process's map names it; the environment always read; a reference said in the logs; what is served runs here; the docs rewritten around the reference | — |
| after | deadline in the context; `grpcs://` | — |

R1–R4 are built — the release gate for PRD_20 is open.

## 7. Roadmap to the release

| Step | What | Unblocks |
|---|---|---|
| 1 | **PRD_23 R1–R4** — the remote bus (built) | any product split across services |
| 2 | **PRD_20** — distributed transactions by signals (`Rollbacks` entrypoint, the coordinator), built on the remote bus and the outcomes | the products that need a sale, an invoice and a stock reservation to succeed or be undone together |
| 3 | Proving both in `sincpro_synthesis`; the skills; the release | adoption |
