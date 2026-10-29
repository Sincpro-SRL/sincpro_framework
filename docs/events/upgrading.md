# Upgrading persistence and events in existing services

Baseline: framework main **e6dbe87**, refactors #145, #146 and #147; manifest version **3.14.5**.
Checked on 2026-10-02. This is a source baseline, not confirmation that this artifact has been
published. Concurrent local saga work is not part of this main baseline.

## 1. Separate the three updates

1. **Guidance:** this repository owns `.claude/skills`. After the reviewed changes reach the
   plugin source, `make skills-update` refreshes the marketplace and installed plugin. Editing
   a local clone does not update the plugin cache. Knowledge MCP guidance has its own refresh
   path; do not assume it changed with this clone.
2. **Dependency:** inspect the consumer's declared range, lockfile and installed artifact.
   Confirm the public imports below before changing application code. A `^3.14.4` range may
   resolve a later artifact, but an existing lock at 3.14.4 remains at that artifact.
3. **Data:** adopting context event tables changes the schema and readers. Treat it as a
   migration with reconciliation and rollback, not a dependency update or class rename.

No consumer dependencies or data were changed as part of this documentation review.

## 2. Public API changes

| Previous guidance | Current contract |
|---|---|
| `from sincpro_framework.events import ...` | `from sincpro_framework.event_driven import ...` |
| `events.faststream` on both sides | sending: `event_driven.adapters.faststream`; `subscribe`: `entrypoints.faststream` |
| `from sincpro_framework.ddd import Repository` | `IRepository` is the read/write port; SQL adapter remains `orm.Repository` |
| Every repository promises every operation | `Analyzes`, `WritesInBulk`, `Transacts` are separate capabilities; inspect `StoreCapabilities` |
| A custom repository class per aggregate | optional `ddd.AggregateRepository[T]` or `orm.DatabaseAggregateRepository[T]` |
| `EventLogEntry` / `event_log_table` wrappers | context base `DomainEvent`, `orm.event_table` and `orm.map_events` |
| `EventTrackableMixin`, `EventStatus`, `delivery_columns` | `DeliverableEventMixin`; the event table includes `delivered_at` and `delivery` |
| `sequence` assigned by `record` | `entity_version` assigned by `EventSourcedMixin.happened`; plain `record` only stamps identity |
| Publishing after the save by hand | `publish_on_commit=` on the repository; `Publisher(queue, forwarding=True)` while the broker is down |
| Separate tables per sourced event and hand-written folds | one context event table and `EventSourcedMixin.apply` / `happened` |

The old imports are not compatibility aliases in this baseline. Smoke-check the selected
artifact in the consumer's environment before starting migration:

```python
from sincpro_framework.ddd import (
    AggregateRepository, DeliverableEventMixin, EventSourcedMixin, IRepository,
)
from sincpro_framework.ddd.repositories import PublishOnCommit
from sincpro_framework.event_driven import Forwarding, Publisher
from sincpro_framework.data_layer.orm import DatabaseAggregateRepository, map_events, template_table
```

Bus-only consumers do not need to install the SQL extra just to run this SQL-specific check.
Forge already declares it; MCP Odoo currently does not use it.

## 3. Transactions and event storage

`context()` defaults to `Writes.SAVED`: mutate and explicitly `save` the aggregate. A pending
session change not saved is discarded at commit with a warning. `Writes.CHANGED` opts into
session-tracked writes, not automatic hooks/cascade or a substitute for aggregate event saving.

Repositories using the **same Database instance** join the active transaction, including
Features called through the bus. Separate Database objects, even with the same URL, do not
provide that shared unit of work. `separate=True` deliberately starts another transaction.

With `map_events(registry, ContextEvent, template_table.event_table(...))`, `save(aggregate)` persists its mapped
recorded events in that same transaction, including mapped change-tracking events. It does
not drain `recorded_events()`. Do not pull before saving or write a second manual history row.
Standalone facts are saved directly: `save([DayClosed()])`.

To publish what a write recorded once it committed, add `DeliverableEventMixin` to the facts
that leave the context and give the repository `publish_on_commit=`; the stored row is stamped
with when each went out. Do not also publish the same events by hand: the repository takes the
ones it hands on. A message still in a forwarding's line when the process dies is not sent, and
consumers stay idempotent.

For gapless document numbering, use `DatabaseNumbering` within the same database transaction
that saves the document. A number allocated outside commits independently.

## 4. Forge

Local inspection found the dependency range `^3.14.4`, locked at **3.14.4**, with SQLAlchemy and
migrations extras. The source locations below are relative to the Forge repository.

- `sincpro_forge/domains/common/infrastructure/events.py` and
  `sincpro_forge/domains/issue/infrastructure/dependencies.py` import the removed `events`
  package. Update imports together with the dependency, then smoke-test bootstrap.
- Auth, coding, delivery, issue, project and workspace maintain manual `...EventEntry`
  wrappers. Those wrappers do not automatically fail after upgrading; adopt the new event
  mapping deliberately and remove conversion writes only when readers and data are ready.
- Issue and Run store ordinary state rows. Retain that model unless rebuilding from a complete
  event stream is a real requirement; adding `EventSourcedMixin` is not a history migration.

Suggested migration sequence:

1. Establish a known package artifact, fix imports, and run existing bus/use-case tests.
2. Inventory each context's event names, schemas, history readers and manual persistence paths
   (including `domains/issue/services/open_issue.py`). Preserve public wire names.
3. Add each context's event table and map its base event class. Migrate old `event_type` and
   payload rows to the new schema with explicit field mapping; preserve ids and causal data
   that actually exist. Do not invent missing stream versions or historical causation.
4. Reconcile event counts, identities, per-entity history and representative payloads. Leave
   historical classes importable. Decide explicitly whether migrated events should be sent: a
   deliverable row saved with no delivery attempt through a repository that publishes on commit
   goes out, and can repeat old side effects.
5. Move readers and writes to the new mapping; remove old wrappers after reconciliation and
   an agreed rollback plan. Prove one fact is persisted once and rolled back with its change.
6. Enable `publish_on_commit` only with idempotent consumers, and a way to find the rows that
   never went out (`delivered_at` empty). Order holds per forwarding line, not across replicas.

## 5. MCP Odoo

Local inspection found `^3.14.4`, locked at **3.14.4**, with observability extras. No current
consumption of event storage, the ORM or `remote_execution` was found in the reviewed source.
Do not add SQL/event infrastructure just to update the framework.

Update the artifact deliberately and verify bootstrap, DTO routing, dependency injection,
auth boundaries and the deployed MCP transport. The consumer's
`docs/21-plan-actualizacion-framework-y-python.md` still describes an older 3.6-to-3.13
migration and needs a separate baseline update. No consumer files were edited here.

## 6. Verified limitations and adoption gates

These are reported for correction, not fixed by this documentation update:

- **Event-name filtering:** on SQLite, a `Criteria` equality filter on `name` is dropped as
  `unsupported_operator`; a base-event query returns other types too. Inspect `dropped` and
  query the event subclass directly until the name-filter contract is corrected.
- **Memory/SQL parity:** two event-sourced aggregates with the same id can append identical
  versions in one memory batch. SQL has a unique stream-version constraint. A green memory
  test does not establish concurrent-writer safety.
- **Replay locking:** `get(EventSourcedAggregate, id, skip_locked=True)` currently bypasses
  the normal locking validation. Rely on optimistic stream versions, not row-lock flags for
  a state row that does not exist.

Further limits: delivery is at least once, not exactly once; multi-replica exclusion needs a
backend with appropriate locks; stored event classes cannot simply be deleted; replay has
no automatic snapshots. A forwarding's line and an in-memory queue live in the process.

Verification evidence: existing `tests/event_driven`, `tests/data_layer/orm/test_events_table.py`,
`test_unit_in_play.py`, `test_aggregate_repository.py`, `test_numbering.py`, and the executable
documentation suite. The limitations above were reproduced using existing test models with
MemoryRepository and temporary SQLite. PostgreSQL concurrency, real brokers, consumer suites
and production migrations were not executed as part of this review.