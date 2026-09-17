# Core: the bus

The part every Sincpro service runs on: one `UseFramework` per bounded context, Features and
ApplicationServices registered by the DTO they answer, dependencies injected by name and typed,
a context that travels with the call, middleware around the execution, error handlers per layer.

| Page | What it answers |
|---|---|
| Root README, [Quick Start](../../README.md#-quick-start) and [Key Features](../../README.md#-key-features-of-the-sincpro-framework) | How to declare a bus, a Feature, an ApplicationService; how dependencies and typing work |
| Root README, [Recommended Infrastructure Structure](../../README.md#️-recommended-infrastructure-structure) | How a bounded context is laid out on disk: `dependencies.py`, `framework.py`, `__init__.py` |
| [Context manager](context-manager.md) | Metadata that propagates through a call with `contextvars`: correlation ids, the user, anything the transport knows |
| [Middleware](middleware.md) | Processing before a Feature or ApplicationService runs: validation, authorisation, caching |
| Root README, [Error Handling](../../README.md#️-error-handling) | Handlers per layer, scoped handlers, chaining |
| [PRD 01, typed dependency container](../prd/PRD_01_typed-dependency-container.md) | Why `UseFramework[DependencyContextType]` exists |
| [PRD 02, middleware system](../prd/PRD_02_middleware-system.md) | Why the middleware pipeline is shaped as it is |

Two facts about the bus that the persistence and events layers rely on:

- **A DTO is routed by its class name.** An event is a DTO, so a Feature registered for an event
  class is a subscriber with nothing else to declare: `@bus.feature([SomeCommand, SomeEvent])`.
- **A bus builds itself once, under a lock, the first time it is called.** Two threads that
  reach it together, as an async fan-out or an event fan-out does, see one fully wired bus.
