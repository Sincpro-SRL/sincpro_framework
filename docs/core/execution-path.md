# The execution path: what happens when a use case runs

Every call to a bus goes through the same path. This page names each piece of it, says which
component owns it, and separates what may **change** the answer (control) from what only
**watches** it (observation). It describes what the code does today; where two registrations
behave differently, the table says so.

## The path of `bus(dto)`

```text
UseFramework.__call__
  1. a reference to a context hosted elsewhere forwards the call (remote_execution)
  2. the bus is built under a lock the first time it is called — and again, as a new
     generation, when something was registered after the last build
  3. outside a scope of this bus, one opens: the bus's node (ContextLevel.BUS) and, when no entrance
     opened the flow, an entrypoint (EntrypointKind.DIRECT)

FrameworkBus.execute                       the facade; marks the outermost call
  4. routes by the DTO's class to the Feature bus or the ApplicationService bus
     (a DTO is refused in both where it is registered)

FeatureBus.execute / ApplicationServiceBus.execute        the same steps, one level each
  5. opens the execution's node: its execution_id, causation_id and correlation_id
  6. opens its span and its metric
  7. providers derive the keys the handler's context lacks; a key the use case declared with
     requires_context and still lacks is refused (ContextRequired)
  8. INCOME   an ExecutionStarted, when someone hears it
  9. interceptors wrap the handler, outermost first
 10. the handler runs
 11a. it raised   → the failure is recorded (span, metric, error report) and marked on the
                    exception with the use case it first failed in; the layer's error handler may
                    answer instead, and then no outcome event goes out for it; otherwise the
                    exception goes up
 11b. it answered → the metric, the span and OUTCOME: an ExecutionCompleted, when someone hears it

back in FrameworkBus.execute
 12. an exception the global error handler may still answer; otherwise, only at the outermost
     call, it is logged once and OUTCOME: an ExecutionFailed goes out when someone hears it
```

An ApplicationService's handler receives the Feature bus. Each Feature it runs repeats steps 5 to
11 as a use case of its own, in the same flow: its `causation_id` is the service's execution.

## Control and observation

The pieces that hang on the path are of two kinds with opposite contracts.

| | Control | Observation |
|---|---|---|
| What it does | Derives, refuses, vetoes, adjusts, answers | Records and announces |
| May change the answer | **yes** | **no** |
| If it fails | the execution fails | it is logged and ignored |
| Delivery | in the call, in order | incomes and outcomes: in the call's thread, no retry, never durable |

| Piece | Kind | Owner | Step |
|---|---|---|---|
| Context providers and `requires_context` | control | `context` | 7 |
| Interceptors | control | `bus_pipeline/interceptors` | 9 |
| The run recorder of a `Pipeline` | control | `pipelines` | 9 |
| Error handlers (global, feature, application service) | control | `error_handler.py` | 11a, 12 |
| `ExecutionStarted` (income) | observation | `bus_pipeline/incomes` | 8 |
| `ExecutionCompleted`, `ExecutionFailed` (outcomes) | observation | `bus_pipeline/outcomes` | 11b, 12 |
| Spans, metrics, error reports, logs | observation | `observability` | 6, 11, 12 |

Incomes and outcomes are **the framework's own** observation: a project listens to them with
functions on its bus. Observability is the signals sent to an external backend (Grafana,
OpenTelemetry, Sentry); `bus_pipeline` does not import it.

The income is announced before the interceptors, so it is the call as its caller made it; the
outcome after them, so it says what the caller received. An interceptor that answers by itself, a
cache for one, still leaves its income and its completion.

Observation is never the place to hold state a use case or a process depends on: a listener may
fail, be slow or not run, and the execution neither knows nor waits. What must follow a commit goes
through the context's event table and its relay ([events](../events/README.md)).

## Who owns what

| Component | Owns |
|---|---|
| `use_bus.py`, `bus.py`, `ioc.py` | the registries of Features and ApplicationServices, the dependencies, building the bus, routing and running a call |
| `context` | the flow: who and for which tenant, the tree from the process to each execution, the execution's identity, providers, stores, drafts |
| `bus_pipeline` | the three layers on the path of a call: interceptors (control), incomes and outcomes (observation) |
| `pipelines` | an ApplicationService that is the order of its steps, and the interceptor the bus attaches to store each run in the context |
| `observability` | the signals sent to external backends: traces, errors, logs, metrics |
| `event_driven` | delivering events: `Publisher`, queues, `Subscriber`, the relay |
| `entrypoints` | everything that starts the application layer from outside: wires, the clock, workers |
| `remote_execution` | a bounded context executed by another service that runs the same code |

Three ways to hand something to a handler, with three different owners:

| It is | You give it with | Reaches the handler as | Travels with the call |
|---|---|---|---|
| An adapter, a client, a connection | `add_dependency` | `self.<name>` | no |
| What the flow knows: tenant, user, language, correlation | `bus.context(...)`, a provider | `self.context`, `use_context()` | yes, as text |
| Configuration ([settings](settings.md)) | `add_dependency` of the settings shape (`self.settings`), or stored in the context under its type | `self.settings`, or `use_context()[Shape]` | no |

A value may be stored in the context under its type (a settings object, a store); it stays in its
process and never travels.

## Entrypoints and adapters

An **entrypoint** starts the application layer from outside and opens the flow: a REST, JSON-RPC,
gRPC or MCP wire, a broker consumer, the clock, a worker, a `Subscriber` receiving an event. An
**adapter** is what the framework calls outward: a queue, a store, a transport to another service.
Inside each component, the folder named `entrypoint/` is the part a project holds (its facade), not
an inbound wire.

## What can be registered, and when

Anything can be registered at any time; nothing registered is ignored.

| Registration | Registered after the bus was built | Repeated by `fresh()` |
|---|---|---|
| `feature`, `app_service` (with or without `replaces=`), `add_dependency` (with or without `replaces=`), `extend` | answers from the next execution | yes |
| `interceptor`, `without_interceptor`, `execution_ids` | answers from the next execution | yes |
| error handlers (`add_*_error_handler`, `without_error_handler`) | answers from the next execution | yes |
| `context_provider`, `context_schema`, `store_context_in` | read by each execution as it opens | yes |
| income and outcome listeners (`on_start`, `on_completion`, `on_failure`, `publish_*`) | read when something is announced | yes |
| the recorder of a `Pipeline` | — attached by each generation when it is built, not registered | each generation attaches its own |

**A generation.** What the built bus holds — its handlers, wrapped by their interceptors, with
their dependencies and error handlers — is one generation. A registration that reaches it asks for
a new one, logged in one line; the next execution builds it, with new handler instances, and takes
the place of the one answering. A request already running finishes on the generation it started
on. A generation that cannot be built raises at the execution that asked for it, and the one
answering stays. `build_root_bus()` brings the bus up to date at a line of its own;
`revision` counts the registrations that reached a built bus, what a snapshot of it — a catalog
of its use cases — compares. How a module uses all of this: [Extending a bus](extending.md).

Listeners also exist for the whole process (`starts`, `completions`, `failures`): every bus of it
tells them too.

## Names

- **The bus's name** is the one given to `UseFramework(...)`, defined once and read through
  `name`. It is the logger's name, the bus name on every signal and every event, the label of its
  node in the context and the prefix of every use case identity.
- **A use case's identity** is `<bus>.<ClassName>` (`registered_name`), what incomes, outcomes, wires
  and the remote bus say. `current_execution().use_case` is the DTO's class name alone.
- **The layer** is `ContextLevel.APPLICATION` (`"application"`) in the context and `"application_service"`
  in events and observability.
