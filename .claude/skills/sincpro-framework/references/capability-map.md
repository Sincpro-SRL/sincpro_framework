# Capability map — one line per capability, and the owner

Use this to find the owner of a task. The **skill** column is a `.claude/skills/` skill in this repo;
the **depth** column is the `docs/` page (every example in those pages runs as a test).

| Capability | Package | Skill | Depth doc |
|---|---|---|---|
| Bus, Feature, ApplicationService, DTO | `sincpro_framework` | `sincpro-framework`, `implement_use_case` | README, `docs/core/README.md` |
| Dependency injection + typing | `ioc.py`, `deps.py` | `sincpro-framework` | README "Recommended Infrastructure Structure" |
| Context manager (correlation, async handoff) | `context/mixin.py` | `sincpro-framework-core` | `docs/core/context-manager.md` |
| Interceptors + `replaces=` | `interceptors.py` | `sincpro-framework-core` | `docs/core/interceptors.md` |
| Error handlers | `error_handler.py` | `sincpro-framework-core` | README "Error Handling" |
| Boundaries, `common/`, domain vs adapters | — | `sincpro-framework` | `docs/architecture/ARCHITECTURE.md` |
| Settings / configuration per context | `settings/`, `conf/` | `sincpro-framework-settings` | `docs/core/settings.md` |
| Aggregates, tables, repository, UoW | `ddd/`, `orm/` | `sincpro-framework-persistence` | `docs/persistence/guide.md` |
| Criteria, Specification, pagination, count | `ddd/criteria/` | `sincpro-framework-criteria` | `docs/persistence/criteria.md`, `specification.md` |
| Relations (FK / id-list / bus / function) | `ddd/entity/relations.py`, `orm` | `sincpro-framework-persistence` | `docs/persistence/relations.md` |
| Hooks + archive/audit/change-tracking mixins | `ddd/repositories/hooks.py`, `ddd/entity/mixins/` | `sincpro-framework-persistence` | `docs/persistence/hooks.md`, `lifecycle.md` |
| Domain events (record/publish/subscribe) | `ddd/events.py`, `events/` | `sincpro-framework-domain-events` | `docs/events/README.md` |
| Change tracking | `ddd/entity/mixins/tracking.py` | `sincpro-framework-domain-events` | `docs/events/change-tracking.md` |
| Event sourcing + event store | `orm` (`event_columns`) | `sincpro-framework-domain-events` | `docs/shapes.md` §3 |
| Outbox + relay | `ddd` `EventTrackableMixin`, `orm` `delivery_columns` | `sincpro-framework-domain-events` | `docs/events/change-tracking.md` |
| Brokers (Kafka/RabbitMQ/Redis/NATS) | `events/faststream/` | `sincpro-framework-domain-events` | `docs/events/brokers.md` |
| Cache + idempotency | `caching/` | `sincpro-framework-caching` | `docs/caching/README.md` |
| Traces / errors / metrics | `observability/` | `sincpro-framework-observability` | `docs/observability/` |
| Entrypoints (REST/FastAPI/RPC/gRPC/MCP/queue) | `entrypoints/` | `sincpro-framework-entrypoints` | `docs/entrypoints/` |
| Bounded contexts across services | `remote_execution/` | `sincpro-framework-entrypoints` | `docs/entrypoints/bounded-contexts-across-services.md` |
| Auth (permissions, providers) | `auth/` | `sincpro-framework-auth` | `docs/auth/README.md` |
| Crons | `cron/` | `sincpro-framework-operations` | `docs/cron/README.md` |
| Migrations across contexts | `migrations/` | `sincpro-framework-operations` | `docs/migrations/README.md` |
| Data analysis (Arrow/pandas/polars/DuckDB) | `data_analysis/` | `sincpro-framework-analytics` | `docs/data_analysis/README.md` |
| Runtime use cases (source on the bus) | `runtime_use_cases/` | `sincpro-framework-analytics` | `docs/runtime_use_cases/README.md` |
| Workflows (Commands composed as JSON) | `workflows/` | `sincpro-framework-analytics` | `docs/workflows/README.md` |
| Introspection | `introspection/` | — | `docs/entrypoints/README.md` |
| System shapes (wiring) | — | `sincpro-framework` | `docs/shapes.md` |
| Testing utilities | `testing/` | — (prompt `framework_critical_testing_strategy`) | `docs/persistence/testing.md` |
| Runtime traps | — | — (resource `framework_gotchas`) | `AUDIT.md` |
