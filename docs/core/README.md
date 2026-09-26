# Core: the bus

The part every Sincpro service runs on: one `UseFramework` per bounded context, Features and
ApplicationServices registered by the DTO they answer, dependencies injected by name and typed,
a context that travels with the call, interceptors around each use case, error handlers per layer.

| Page | What it answers |
|---|---|
| Root README, [Quick Start](../../README.md#-quick-start) and [Key Features](../../README.md#-key-features-of-the-sincpro-framework) | How to declare a bus, a Feature, an ApplicationService; how dependencies and typing work |
| Root README, [Recommended Infrastructure Structure](../../README.md#-recommended-infrastructure-structure) | How a bounded context is laid out on disk: `dependencies.py`, `framework.py`, `__init__.py` |
| [Context manager](context-manager.md) | Metadata that propagates through a call with `contextvars`: correlation ids, the user, anything the transport knows |
| [Interceptors](interceptors.md) | Code around one use case, from outside it: veto, adjust input or response, audit, cache, retry — with runnable recipes |
| Root README, [Error Handling](../../README.md#-error-handling) | Handlers per layer, scoped handlers, chaining |
| [PRD 01, typed dependency container](../prd/PRD_01_typed-dependency-container.md) | Why `UseFramework[DependencyContextType]` exists |
| [PRD 04, extension points](../prd/PRD_04_extension-points.md) | Why interceptors replace middleware, and what `replaces=` adds |

Two facts about the bus that the persistence and events layers rely on:

- **A DTO is routed by its class name.** An event is a DTO, so a Feature registered for an event
  class is a subscriber with nothing else to declare: `@bus.feature([SomeCommand, SomeEvent])`.
- **A bus builds itself once, under a lock, the first time it is called.** Two threads that
  reach it together, as an async fan-out or an event fan-out does, see one fully wired bus.
