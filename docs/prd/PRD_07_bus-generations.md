# PRD_07: Bus generations — reload without stopping

- **Status**: proposal
- **Depends on**: nothing; PRD_06 is its main consumer
- **Research**: scratchpad `11_runtime_loading_reload.md` (probes: `probe_bus_reload.py`,
  `probe_sqlalchemy_remap.py`)

## Problem

When a definition changes (PRD_06), the bus that serves requests has to change with it — while
requests are running, on every replica. Today a bus is built once and cannot be rebuilt:

| Measured | Result |
|---|---|
| Register a Feature after the bus was built | `UnknownDTOToExecute`, although the container lists it |
| Force a rebuild of the same instance | the Singleton feature bus keeps the old registry; resetting it changes what running requests use |
| Build a **new** `UseFramework` and swap it while 8 threads execute | the running request finishes on the old one, the next answers the new one — **0 errors in 1 600 swaps**, ~0.95 ms per build |

So the mechanism exists already: a bus is cheap to build and safe to swap. What is missing is a
supported way to do it, and to tell the other replicas.

## Goals

1. Replace the bus of a bounded context while it serves, with no request seeing a half-built one.
2. A failed rebuild never replaces a working bus.
3. Every replica converges on the same version.
4. Entrypoints (RPC, MCP, gRPC, scheduler) serve the current bus without being restarted.

## Non-goals

- Rebuilding a bus in place. `build_root_bus()` stays one-way.
- Reloading Python modules (`importlib.reload`): old instances keep old classes and every registry
  here is keyed by class.
- Changing engines, connection pools, observability or code-defined mappings at runtime.

## Prior art

| | How | What we take |
|---|---|---|
| Odoo | Registry per database; a Postgres sequence (`base_registry_signaling`) checked before every request; a running request keeps the registry it holds | A version compared per request; readers pin what they started with |
| Copy-on-write / RCU | Build the new structure aside, swap one reference | The swap |
| Postgres `LISTEN/NOTIFY` | Delivered on commit, 8 kB payload, lost when nobody listens | A **wake-up hint**, never the source of truth |

## The model

A **generation** is one immutable, fully built bus plus the version of the definitions it was
built from. A **`BusRegistry`** holds the current generation of one bounded context and knows how
to build the next one.

```python
def build_billing(definitions: Definitions, shared: Shared) -> UseFramework[BillingDeps]:
    bus = UseFramework[BillingDeps]("billing")             # same name every generation
    register_billing(bus)                                  # the same shipped classes
    bus.add_dependency("database", shared.database)        # shared, never rebuilt
    bus.add_dependency("repository", Repository(shared.database, rules=definitions.rules()))
    return bus


billing = BusRegistry(
    "billing",
    build=build_billing,
    shared=shared,
    source=DatabaseDefinitions(database),
    signal=DefinitionVersionSignal(database, listen=True, poll_every=timedelta(seconds=5)),
)
billing.start()

billing.current(CommandCreateInvoice(...))               # the generation in force, pinned for the call
billing.current.number, billing.current.definitions_version
billing.reload()                                         # build → check → swap, or raise and keep the old one
```

- **The factory is the one used at startup.** There is no second wiring path: a generation is what
  `build(definitions, shared)` returns.
- **Shared** is what outlives generations — `Database`, HTTP clients, the logger. Passed in, never
  rebuilt; so connection pools and Sentry clients stay.
- **Readers pin once.** A request takes `registry.current` at its start and finishes on it; the old
  generation is released when its last request ends.
- **The bounded-context name is the same in every generation**, so observability keeps one identity.

## A reload

1. Under the registry's lock, one builder at a time.
2. Load the definitions; if their version is the current one, stop.
3. Build the bus with the factory and `build_root_bus()`.
4. Check it: every definition validated against this bus (PRD_06), an optional smoke check.
5. Final: swap the reference and log `billing: generation 12 → 13 (definitions v47)`. On any
   failure: keep generation 12, report the failure with the version that failed.

## Across replicas

- The **source of truth** is a `definition_version` bumped in the same transaction as the change.
- `NOTIFY` wakes the replicas up; a poll every few seconds catches what a missed notification lost.
- Every log line and span carries `definitions_version`; a message routed by name (queue, outbox)
  carries it too, so a consumer on an older generation can tell.

## Entrypoints

`RpcGateway`, `McpGateway`, `GrpcGateway` and `SchedulerGateway` accept a `BusRegistry` wherever they
accept a `UseFramework`, and resolve the current generation per call. MCP and gRPC, which publish
their tool list when the server starts, re-publish it on swap (MCP `tools/list_changed`, gRPC
reflection) or keep a stable generic `execute`.

## Threads

- Swapping one reference is atomic; readers take no lock.
- Builds are serialized by the registry's lock and never touch a generation in use.
- Free-threaded Python 3.14: the model holds, but `dependency-injector` re-enables the GIL when
  imported (`ioc.py` notes it) — no worse than today.

## What a generation must not do

- Register error handlers or interceptors **after** it was built — they belong in the factory, or
  they are lost on the next swap. PRD_04 makes a late registration an error.
- Hold state across generations on a Feature instance (PRD handler lifetime: state lives in locals).

## Phases

1. `BusRegistry`, `Generation`, `reload()`, factory contract, logs and spans.
2. `DefinitionVersionSignal` (sequence + `LISTEN/NOTIFY` + poll).
3. Entrypoints accept a registry; MCP / gRPC re-publish on swap.

## Open questions

- One registry per bounded context (recommended) or one for the whole process?
- Should a reload be allowed to change which Features exist (a definition that enables an addon),
  or only the definitions they read?
