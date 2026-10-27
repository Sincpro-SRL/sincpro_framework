# Core: the bus

The part every Sincpro service runs on: one `UseFramework` per bounded context, Features and
ApplicationServices registered by the DTO they answer, dependencies injected by name and typed,
a context that travels with the call, interceptors around each use case, error handlers per layer.

| Page | What it answers |
|---|---|
| Root README, [Quick Start](../../README.md#-quick-start) and [Key Features](../../README.md#-key-features-of-the-sincpro-framework) | How to declare a bus, a Feature, an ApplicationService; how dependencies and typing work |
| Root README, [Recommended Infrastructure Structure](../../README.md#-recommended-infrastructure-structure) | How a bounded context is laid out on disk: `dependencies.py`, `framework.py`, `__init__.py` |
| [Features and ApplicationServices](features.md) | What answers a Command: the two layers, registering, what a handler has (dependencies, context, `feature_bus`), one instance per generation, `replaces=`, registering after the build, replacing an adapter — and why it is this way |
| [Execution path](execution-path.md) | What happens when a use case runs, step by step; what may change the answer (control) and what only watches it (observation); which component owns what; what is registered when |
| [Context](context-manager.md) | The context as a component: `use_context()` from anywhere, a tree from the process to each execution, threads, providers, stores, carried on every transport; the execution identity |
| [Pipelines](pipelines.md) | An ApplicationService that is the order of its steps, declared as data: `Pipeline`, `steps`, `always`, `by`, `resume`; the run stored in the context (`PipelineRuns.of()`) by an interceptor the bus attaches, for every shape of system — no tenant, no user, a user across tenants |
| [Dry run](dry-run.md) | A Command that says what it would do: `class CommandX(DryRunnable)` and a `preview` on its handler; `dry_run=True` answers a `Preview` through the same guards, `execute` never runs; previewed by default on a project's own base |
| [Sessions](sessions.md) | A sign-in that lasts while it is used: `use_context().sessions()` — sliding `idle_for` (30 days), an optional `at_most`, `idle_for=None` until closed; `take` for a code used once; on the store the context is stored on |
| [Leases](leases.md) | A name held by one owner at a time across every replica: `Leases` on any `KeyValueStore`, renewed in the background, `FREE` / `HELD` / `EXPIRED` and a fence per take; `lease =` on a `Pipeline` makes it one run of a `run_id` at a time |
| [Circuit breaker](circuit-breaker.md) | Stop calling a dependency that fails repeatedly and try it again once it may be back: `CircuitBreaker` as a block, a decorator, a call or an adapter's own; its state where the context stores its leases; `CircuitOpen` is `UNAVAILABLE` with its `retry_after`; a bus hosted elsewhere when asked — `?circuit_breaker=on` on its address, or `hosted_by(..., circuit_breaker=...)` |
| [Caches and idempotency](../caching/README.md#the-simple-way-buscache-and-busidempotency-nothing-to-build) | What every bus stores beside its context with nothing built: `bus.cache` (`@billing.cache`, `.ttl`, `.sliding`, `.versioned`, `fail_safe=`, `.queries(Query)`) and `bus.idempotency` (`@billing.idempotency`, `.exactly_once(records)`), the service's `cache` / `idempotency`; stored where the bus stores its context — its own store, else the process's (`store_context_in(redis)`), else memory — apart by the bus's name |
| [Incomes](incomes.md) | What a use case was handed, as an event: `ExecutionStarted` for every use case that begins, before the interceptors — `@bus.on_start`, `start_listeners.subscribe`, `bus.publish_starts(to=…)` |
| [Outcomes](outcomes.md) | What a call did as events: `ExecutionCompleted` for every use case that answered (DTO and response), `ExecutionFailed` for the exception that escaped — `@bus.on_completion` / `@bus.on_failure`, x, `bus.publish_completions` / `publish_failures(to=…)`; never a failure an error handler contained |
| [Settings](settings.md) | One document, any shape: the global object, a context's own section, the cascade, the environment by path, secrets, where each value came from, and the framework's own settings read from the project's |
| [Drafts](drafts.md) | What a form saves between requests: a context stored by whose it is — `self.context.drafts()` by user, `by=["session_id"]` by session — where the context is stored; optimistic versions, a TTL |
| [Exceptions](exceptions.md) | Who caused a failure (`ProgrammingError`, `ClientError`, `DomainError`, `ExternalServiceError`) and what to do about it (`failure_kind`), the same on every wire; a crash is everything else |
| [Error handlers](error-handlers.md) | The three chains (feature, application service, global) a failure crosses from the inside out; answering or re-raising, and the trap of a handler that only watches; order, `replaces=`, switching one off; what is recorded of a contained failure — and why it is this way |
| [Interceptors](interceptors.md) | Code around one use case, from outside it: veto, adjust input or response, audit, cache, retry — with runnable recipes |
| [Extending a bus](extending.md) | Modules that plug in: what a module is, what it can add at each step of the path, installing it before or after the bus was built, replacing a use case or an adapter, where its state lives, how it is proven in its own CI |
| Root README, [Error Handling](../../README.md#-error-handling) | Handlers per layer, scoped handlers, chaining — what contains a failure; [Outcomes](outcomes.md) is what is said when one escapes |
| [PRD 01, typed dependency container](../prd/PRD_01_typed-dependency-container.md) | Why `UseFramework[DependencyContextType]` exists |
| [PRD 04, extension points](../prd/PRD_04_extension-points.md) | Why interceptors replace middleware, and what `replaces=` adds |

Two facts about the bus that the persistence and events layers rely on:

- **A DTO is routed by its class name.** An event is a DTO, so a Feature registered for an event
  class is a subscriber with nothing else to declare: `@bus.feature([SomeCommand, SomeEvent])`.
- **A bus builds itself under a lock, the first time it is called — and again, as a new
  generation, after something was registered on it.** Two threads that reach it together, as an
  async fan-out or an event fan-out does, see one fully wired bus.
- **Inside an ApplicationService the feature bus is called like the root bus.**
  `self.feature_bus(CommandIssue(...), ResponseIssue)` is `self.feature_bus.execute(...)`, both
  forms typed, so moving a call between a Feature's caller and an ApplicationService changes
  nothing but the receiver.
