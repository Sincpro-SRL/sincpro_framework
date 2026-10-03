# Framework audit — findings, fixes, and what needs a decision

## 2026-10-02: persistence and event refactor review

**Baseline:** main `e6dbe87` (#145-#147), manifest `3.14.5`. The initial tree was clean.
Concurrent local saga implementation appeared during this review; it is not part of that
main baseline and was not authored or reverted by this documentation task.

**Decision:** the refactored API is usable, but do not migrate a strict-ordering event stream
on the assumption that the relay currently enforces that ordering. Documentation and skills
were updated; the following runtime findings are **reported, not fixed**.

### Confirmed findings

1. **P1 [side-effect] Retry backoff does not hold the entity stream across passes.**
  [EventRelay._due](sincpro_framework/event_driven/entrypoint/relay.py#L113) excludes not-yet-due
  predecessors and [_carried_out](sincpro_framework/event_driven/entrypoint/relay.py#L146)
  rebuilds its held set each pass. Using the existing relay test models, `O-1/dhl` failed
  with a ten-second backoff and held `O-1/ups` in pass one. Without advancing the clock,
  pass two delivered `ups` while `dhl` remained pending. This can invert dependent effects.
  **Fix direction:** account for blocking predecessors beyond the current due batch, and
  validate entity ordering across passes and PostgreSQL replicas. `SKIP LOCKED` on rows
  alone does not establish a per-entity stream lock.

2. **P2 [type-error] Event `name` equality filters are dropped, widening the query.**
  [map_events](sincpro_framework/orm/sqlalchemy/services/event_mapping.py#L100) maps the column
  under `wire_name`, while the event's `name` remains its class-level wire identity. With
  `Paid` and `DayClosed` stored in temporary SQLite, querying `LedgerEvent` with
  `Condition(field="name", value=Paid.name)` returned **both** types and
  `Dropped(field='name', reason='unsupported_operator')`. Querying `Paid` directly returned
  only `Paid`. This is not an observed `WHERE false` bug: the public filter is rejected
  before it narrows the query. Callers must inspect `dropped`.
  **Fix direction:** align event metadata, supported operators and mapped attribute lookup;
  protect the public Criteria contract with a mixed-event-table regression case.

3. **P2 [side-effect] The memory store accepts duplicate stream positions in one batch.**
  [_events_of_sourced](sincpro_framework/ddd/repositories/memory_repository.py#L680) checks
  persisted events but not earlier pending entries in the same batch. Two new wallets with
  id `W-1`, credited 10 and 20, were accepted by `save([first, second])`; `get` rebuilt
  **balance 30, version 1**. Both events occupied version 1. SQL's unique constraint rejects
  that shape, so a memory-only test can give false confidence.
  **Fix direction:** validate occupied stream positions across the whole batch before
  mutating storage; keep a parity case against SQL.

4. **P2 [side-effect] Event-sourced reads bypass locking-option validation.**
  [Reading.get](sincpro_framework/orm/sqlalchemy/services/workflows/reading.py#L760) returns
  through replay before the usual locking path. On a persisted SQLite wallet,
  `get(Wallet, "W-1", skip_locked=True)` succeeded outside a unit of work without
  `for_update=True`. The caller's locking request is silently ignored.
  **Fix direction:** explicitly reject unsupported replay locks or define a real event-stream
  locking contract. Continue using optimistic stream versions; do not imply a state-row lock.

### Reproduction and verification

The findings were reproduced without editing runtime code, using models already in
[test_relay.py](tests/event_driven/test_relay.py) and
[test_events_table.py](tests/orm/test_events_table.py), MemoryRepository and temporary SQLite.
No PostgreSQL service, real broker or consumer suite was used.

- Relay: repeat `run_once()` before advancing the ten-second retry clock, unlike the existing
  happy retry test which jumps directly to the due time.
- Name filter: store both event classes; assert the base query's returned types and `dropped`,
  then compare with querying the subclass directly.
- Batch parity: create two `Wallet(id="W-1")`, credit each, save together and inspect replay.
- Lock options: persist a sourced wallet, then request `skip_locked=True` without `for_update`
  or `context()`; the call currently returns the wallet rather than refusing.

Documentation validation: 54 documents, 130 unique imports from parseable Python examples,
all skill frontmatter and parsed local links passed. Plugin manifests passed with the existing
warning about their intentionally absent version. Focused executable documentation and
transaction/event tests: **56 passed, 6 skipped**; one Starlette/httpx deprecation warning.
Additional focused repository, numbering and broker tests: **75 passed, 8 skipped**.

`make verify-format` completed formatters and Pyright with **0 errors and 0 warnings** in
runtime and tests, then failed its dirty-tree condition. It did not establish a clean release
gate; documentation edits and concurrent work remain uncommitted. No commit was made.

### Consumer upgrade decision

Both inspected consumers declare `^3.14.4` and lock `3.14.4`; registry publication of the new
source was not verified. Forge has removed-package imports and manual history wrappers;
MCP Odoo did not show ORM/event usage in the reviewed source. The migration guide separates
dependency compatibility, data migration and optional event sourcing:
[docs/events/upgrading.md](docs/events/upgrading.md).

### Engineering rationale

The changes align the published contract, not runtime behavior: **Repository** and **Unit of
Work** decide durable state; **Event Sourcing** rebuilds it; **Transactional Outbox** separates
commit from delivery; a **Strategy** decides failures. Conflating these patterns hid the
difference between stored history, replay and ordered delivery. In accordance with
`sincpro_architecture_guidelines`, `sincpro_critical_testing_strategy` and
`sincpro_teaching_mode`, verification targeted rollback, ordering, filters and parity rather
than adding abstractions or treating test count as proof of production maturity.

## 2026-10-02: maturity assessment

**Recommendation: controlled adoption, with hardening required before broader rollout.**
The synchronous bus and aggregate persistence have substantial behavioral coverage; this is
not a prototype. That does not make every execution boundary equally mature. Confirmed
isolation, confidentiality and ordering defects prevent a blanket production-readiness claim.
The four event/persistence findings above remain open; the seven additional findings below
bring this review to **11 reproduced runtime defects**. None was fixed in this task.

### Additional confirmed findings, by risk

1. **P1 [side-effect] Hidden context values appear inside DEBUG log messages.**
  [FrameworkContext.__enter__](sincpro_framework/context/framework_context.py#L55) interpolates
  the raw context dictionary. With `hide_in_logs=["token"]` and a synthetic marker, the
  marker appeared in the captured `event` message. Structured-field filtering is not whole
  message redaction. The existing test checks that records lack a `token` key, not that the
  value is absent from their serialized content.
  **Fix direction:** omit or redact the raw mapping and assert the marker is absent from
  the entire captured log, not merely from its top-level keys. Do not put secrets in context.

2. **P1 [side-effect] Async execution does not preserve root per-call context isolation.**
  [UseFramework.get_async_bus](sincpro_framework/use_bus.py#L802) wraps the internal bus;
  [AsyncBus.execute](sincpro_framework/aio/bus.py#L75) invokes it without the root call's
  context boundary. A handler that reads and then writes `self.context["stamp"]` returned
  `None` on the second synchronous root call, but returned `"first"` on the second async
  call without an explicit context. Request state can leak between calls.
  **Fix direction:** preserve the root execution boundary in the facade and test cleanup,
  inheritance and concurrent calls. Copying ContextVars does not deep-copy the dictionaries
  they reference; an explicit shared mutable scope is not a concurrency fix.

3. **P1 [side-effect] Invalid secret configuration is logged in plaintext.**
  [SincproConfig.resolve_env_variables](sincpro_framework/settings/domain/config.py#L99)
  logs `env_value!r` when validation fails before falling back to the default. With a
  `Secret[int]` field and a synthetic invalid environment value, the raw marker appeared
  in the INFO log. Masking a successfully constructed Secret does not protect this path.
  **Fix direction:** log the variable name and validation reason, never its raw value;
  test invalid and absent secret paths as well as successful masked serialization.

4. **P2 [side-effect] Equal nested scopes remove the wrong registered overlay.**
  [_pop_overlay](sincpro_framework/context/mixin.py#L138) removes by dictionary equality.
  Entering `context({"tenant": "A"})`, then entering/exiting `context({})`, then opening
  `context({"flag": True}, global_scope=True)` left the active outer context's flag at
  `None`. The equivalent inner dictionary caused the outer one to be unregistered.
  **Fix direction:** remove the exact overlay by identity, and test global publication
  and restoration after identical nested scopes. Global scope remains unsuitable for
  request-specific data even after this defect is corrected.

5. **P2 [side-effect] A remote handler's LookupError becomes a missing-context response.**
  [HTTP host](sincpro_framework/remote_execution/entrypoint/http.py#L89) catches any
  `LookupError` around both destination resolution and handler execution. A registered
  Feature raising `KeyError` returned HTTP **404** through the real ASGI route in TestClient.
  The [HTTP adapter](sincpro_framework/remote_execution/adapters/http.py#L120) maps that status
  to `ContextUnavailable`, losing the intended classification of the business failure.
  **Fix direction:** distinguish destination lookup errors from handler exceptions. The
  analogous gRPC catch needs the same regression case; only HTTP was reproduced here.
  The HTTP adapter does not automatically retry the command.

6. **P2 [type-error] A malformed service token raises AttributeError, not Unauthenticated.**
  [ServiceTokenProvider._claims](sincpro_framework/auth/adapters/service_token_provider.py#L98)
  assumes decoded JSON is an object. Synthetic token `W10.e30.x` decodes its header to a
  list and raised `AttributeError` before signature validation. This is error handling,
  **not a demonstrated authentication bypass**.
  **Fix direction:** validate header/claim shapes and normalize malformed input to
  `Unauthenticated`; cover list/null/scalar JSON as well as bad signatures and expiry.

7. **P2 [side-effect] ThreadContextBus rewrites a handler RuntimeError as a concurrency error.**
  [ThreadContextBus.execute](sincpro_framework/context/thread_context_bus.py#L62) catches all
  `RuntimeError` from `Context.run`. A single-threaded handler failure was replaced with
  the message that the snapshot was entered concurrently; the original survived only as
  `__cause__`. The reproduction uses a built `framework.bus.thread_context()`;
  `UseFramework` itself has no `thread_context` method.
  **Fix direction:** distinguish entry-to-context failure from callback failure and verify
  that an ordinary handler RuntimeError retains its original classification/message.

### Evidence and maturity by surface

| Surface | Assessment | Evidence and remaining gate |
|---|---|---|
| Synchronous bus, DI, DTO routing | Established functional foundation | Handler lifetime, late registration, context and compatibility suites pass locally; adapters shared across requests still need their own concurrency guarantees |
| Async/thread execution | Conditional | Propagation tests pass, but isolation and error-preservation defects above need regression cases and fixes |
| SQL aggregate persistence | Substantial local coverage | Shared UoW, rollback, versioning, typed views and numbering tested; PostgreSQL locking/concurrency not certified by this run |
| Event storage and sourcing | Recent, partially hardened | Save/replay/rollback tests pass; name queries, replay lock validation and memory parity are open; no automatic snapshots |
| Relay and distributed effects | Conditional | Retry/park/replay are exercised; strict ordering fails across backoff passes, and crash/redelivery behavior needs deployment-backend evidence |
| Auth and observability | Functional, confidentiality hardening required | Guards, permissions and signal contracts have tests; plaintext-log paths and malformed-token handling remain open |
| HTTP/gRPC/MCP and brokers | Integration foundation | Local transport tests and mocked broker settlement exist; production TLS, credential rotation, broker durability and shared-inbox failover remain separate gates |
| Skills and documentation | Aligned to the reviewed main source | Imports, plugin structure, links and executable docs checked; local edits are not yet distributed to plugin/MCP consumers |
| Release and consumer compatibility | Not certified | Manifest version 3.14.5 predates the refactors; registry artifact not inspected, Forge/MCP Odoo still lock 3.14.4 |
| Sagas | Outside this main-baseline assessment | Concurrent local implementation exists and was preserved; no independent crash/compensation/concurrency audit of it was performed |

### What was run

`make test COVERAGE_ARGS='--no-cov -q'` on the current working tree:
**2657 passed, 23 skipped, 45 deselected, 5 warnings**, in 63.91 seconds. The selector excludes
realworld tests. This is the working tree, including concurrent additions, not an isolated
checkout or a built wheel of `e6dbe87`. Coverage was intentionally disabled to avoid rewriting
the tracked report; no coverage percentage is claimed.

The warnings were one Starlette/httpx deprecation and four intentional legacy ValueObject
deprecations. The earlier `make verify-format` ran formatters and Pyright successfully and
then failed the dirty-tree condition. Subsequent edits from this task were Markdown only.
Standalone reproduction snippets exercised the findings with synthetic data and temporary
stores; no real secrets were inspected and no persistent regression tests were added.

Not run: realworld/stress suites, PostgreSQL multi-replica cases, real broker recovery,
production credentials/TLS, consumer test suites, package publication or data migration.
The generated OpenWiki was not refreshed; maintained docs and source/tests are authoritative.
This is a risk-based audit, not an exhaustive security review of every module.

### Prioritized next actions

1. Correct plaintext-log paths and per-call async isolation, with focused regression tests.
2. Correct relay ordering before dependent effects or financial streams rely on it; exercise
  backoff, crash-after-send and multiple PostgreSQL workers.
3. Correct event filtering, batch-version parity and exception/locking classification;
  retain tests that failed before the fix, not only existing happy paths.
4. Produce a traceable release artifact and smoke-test its selected extras in the two
  consumers. Follow the [upgrade guide](docs/events/upgrading.md); reconcile Forge history
  before retiring manual wrappers or sending historical facts.
5. Audit sagas separately once that work is stable: command idempotency, durable pending
  effects, duplicate event consumption, deadlines, failed compensation and crash recovery.

### Why these gates matter

The architectural strength is **Facade + DI + scoped execution context**, backed by a
**Repository/Unit of Work** boundary. The cost is that every facade must preserve isolation,
error classification and redaction, not merely return the right DTO. **Transactional Outbox**
proves atomic storage, not ordered or exactly-once delivery. Following `sincpro_code_review`
and `sincpro_teaching_mode`, the report names observable failures and how to verify their
correction rather than converting a large green test count into a maturity score.

## Historical audit (predates the event refactor)

The notes below are preserved as history. Their old event-entry, status and manual-relay
recipes are superseded by the dated review above and current event documentation. Historical
test counts and claims of fixes are not results of the current audit.

Not documentation: a working note for review. Delete it once the decisions in §3 are made.

Everything in §1 and §2 was measured by running it. Nothing in §3 has been done.

**Seven areas audited: errors, observability, middleware, concurrency, entrypoints, naming,
documentation.** The suite went from 1027 to 1037 tests, pyright clean throughout.

**The one to read first, if you read one thing:** a JSON-RPC client could read database
credentials and SQL out of any internal failure. Fixed and pinned — §"entrypoints" below.

**The one that needs you:** `bus.ignore_sentry_exceptions(...)` names a vendor in a public API.
§3a.

---

## 1. Fixed, and pinned with a test

| | |
|---|---|
| A relay claimed without an order, so it **delivered facts out of order** | Claims in `id` order; anything that folds them is now correct |
| A history helper read without an order | Same fix at the source — a history without an order is not a history |
| A misdeclared relation raised `KeyError: '_sincpro_resolved'` | Says which declaration to check |
| There was no `event_columns()` / `delivery_columns()` | Storing an event needed knowing `label` must be `JsonText`; declaring it `Text` failed at the insert naming a parameter number |

## 2. Verified to already work — documented rather than built

The rule this audit follows: **if the framework already answers it, the answer is a documented
recipe, not a new abstraction.** Each of these was run before being believed.

- **A dead letter.** `attempts` + `mark_failed` + `mark_cancelled` are the whole vocabulary.
  Measured: retries to a cap, then gives up as `cancelled`, and no later claim picks it up.
  Pinned in `tests/ecosystem/case_3_distributed`.
- **An event store.** A `DomainEvent` is an `Entity`; the repository stores and queries it.
- **A transactional outbox.** `EventTrackableMixin` + `search(for_update, skip_locked)`.
- **Composite-key aggregates.** `get(Thing, ("fp1", "a"))` works today.
- **Append-only.** A four-line `Rule` on `before_save`.
- **Retry on a lost race.** `Repository.retrying(...)` exists.
- **No dead code.** Two module helpers looked unused; both are invoked by pydantic
  (`@model_validator`, `@model_serializer`). Removing either would have broken validation or
  serialization — *"used zero times" is not a signal where a framework does the calling.*

### Audited: error handling and observability

**Exception swallowing — clean.** Twenty-two handlers neither re-raise nor report; twenty are in
`observability/tracing/` and that is correct by design (observability must never take down the
thing it observes). The other two are a validation predicate returning `False`, which is its
job, and a log tag falling back to `""`, which must not break a query.

**One real trap, documented rather than changed** (it is a contract, not a bug):

    app.add_global_error_handler(lambda error: log.error(error))
    app(SomeCommand())        # returns None. The failure is gone and nobody knows.

With no handler the exception propagates. With one, **what it returns becomes the bus's
answer** — and a handler written to *watch* errors returns `None` without meaning to. The
contract was documented in `error_handler.py`, where the person registering a handler never
looks; it is now on the three `add_*_error_handler` methods, with the trap named and the
`raise` shown. Pinned by three tests.

### Audited: middleware

**A silent corruption, fixed.** `MiddlewarePipeline` rewrites `dto.__class__` back to the
registered type when a middleware answers with a different one. That is deliberate and tested:
it lets a middleware *enrich* a DTO into a wider type while the registry still routes on the
original. It works because the wider type has everything the original declared.

Answer with an unrelated type and the rewrite produced an object that **said it was the
original and had none of its fields**:

    bus.add_middleware(lambda dto: Unrelated())
    # the Feature receives something whose type says `Order` and whose `amount` does not exist

The failure then surfaced as `AttributeError` inside business logic, with nothing pointing back
at the middleware. The rewrite now refuses when the answer is missing a declared field, naming
which. Enrichment is untouched — both cases are pinned.

### Audited: concurrency

Measured, not assumed — this is the area where the worst bugs of the session were.

- **A cold bus called by eight threads at once: no failures.** The `RLock` in `build_root_bus`
  holds. The warning in `pre_start`-style docs is more conservative than the behaviour.
- **Eight threads × 200 writes to the in-memory store: nothing lost.** Dict operations are
  atomic under the GIL.
- **Optimistic locking in the double matches the engine** — for the shape that can actually
  race.

**One divergence, documented rather than changed.** `MemoryRepository.get` hands back the record
it holds, not a copy, so two callers in one process share one object and no `StaleAggregate` can
happen between them. Against a database each session builds its own instance and the race is
real. A test that wants the race holds `copy.deepcopy` of each read — now written in the
module's own docstring, and pinned both ways.

Making `get` return a copy would be a direction change: the store skips the version check when
`held is record` precisely *because* it hands back the same object. Listed in §3 rather than
done.

### Audited: observability — what leaves the process

The same question the entrypoint leak came from, asked of every place data can escape.

- **Error reports carry names, never values** — the DTO's *name*, the bus, the artifact, the
  tenant. Nothing of what the DTO held.
- **Spans carry the same**: layer, instance, DTO name.
- **SQL travels without its values.** `after_cursor_execute` hands the statement and the
  parameters separately, and only the statement is logged or attached:
  `INSERT INTO thing (…) VALUES (?, ?, ?)`. Measured, not read.

Clean. The twenty swallowed exceptions in `tracing/` are also correct: observability must never
take down what it observes.

### Audited: naming

**The public surface has no cryptic names.** Every parameter of every public function was
checked; the only short ones are `group_by(target, by=…)` and `retrying(…, on=…)`, which read
as English. The naming problems are the three methods in §3a, not a habit.

### Audited: documentation

**Every import in every doc was resolved against the package** — 38 of them, 1 broken, and that
one is in `docs/prd/`, which the index already labels history. It now says so at the top and
points at what exists instead.

**Two gaps from this session's own changes, closed.** `docs/events/README.md` still showed only
`SyncQueue(Subscriber(...))` — not the function form that cuts the wiring circle — and said
nothing about an event's two identities, which is what makes a fact reach a context that cannot
import the publisher's class. Both are now there, with the example run before it was written.

### Audited: entrypoints — one credential leak, fixed

**A JSON-RPC client could read the inside of the process.** Any exception a Feature raised had
its own text sent as the error's `data`. A database failure's text carries the connection
string and the statement:

    "data": "(builtins.Exception) postgres://admin:hunter2@10.0.0.5/prod refused
             [SQL: SELECT * FROM users WHERE token = 'secret-123']"

Password and secret, to anyone who can make an internal error happen.

Now a `DomainError` keeps its message — it was written for whoever asked — and **anything else
says nothing at all**. `logger.exception` already had the whole thing, which is where it
belongs. The docs said only "`-32603` Internal error", so the leak was never a promise; both
halves are pinned, and MCP was checked and does not do this.

## 3. Needs your decision — nothing here has been changed

### 3a. Three public names that do not say what they are

These are **breaking changes to a published API**, which is why they are a proposal.

| Today | Problem | Proposed |
|---|---|---|
| `bus.ignore_sentry_exceptions(...)` | **Names a vendor in the public API.** The framework supports Sentry *and* GlitchTip, and a third tomorrow. What the caller means is "these are expected, never report them" | `bus.never_report(...)` |
| `bus.build_root_bus()` | Leaks internal structure — a caller does not know or care that there is a "root bus". What they are doing is making it ready before concurrent work | `bus.warm_up()` |
| `bus.map_to_dto_or_event(name, payload)` | Describes the implementation's branch, not the intent. It rebuilds whatever was registered under a wire name | `bus.rebuild(name, payload)` |

**Blast radius:** `sincpro_synthesis` calls `ignore_sentry_exceptions` in every context's
`framework.py`, and `build_root_bus` in `pre_start.py` and its CLI. Both are mechanical edits.

**My recommendation:** do all three, and keep the old names as thin aliases that warn, for one
release. The vendor leak is the one I would not leave — it is the kind of thing that is
embarrassing in an open-source API and impossible to remove later.

### 3b. What a production service needs and this does not have

Measured by looking, not guessing. Ordered by how often a real service needs it.

| | Status | My read |
|---|---|---|
| **Graceful shutdown** | `BackgroundQueue.stop()` only | A service needs one call that drains every queue and closes every engine. Today each is stopped by hand and nothing coordinates them. **Worth building.** |
| **Health / readiness** | nothing | Arguably the entrypoint's job, not the framework's — but every consumer will write the same twenty lines. **Worth a recipe, probably not a feature.** |
| **Idempotency of a command** | nothing | The outbox makes *delivery* safe; a command replayed by a caller is not. A `Feature` that is asked twice runs twice. **Worth a decision.** |
| **Rate limiting / circuit breaker** | nothing | `Middleware` exists and is the right seat for both. **A recipe, not a feature.** |
| **Scheduled work** | nothing | You mentioned Temporal. This is a different product, not a gap — but the framework should say so rather than leave people guessing. |

### 3c. Should the in-memory double hand back copies?

Today it hands back the object it holds. That makes it a faithful dict and an unfaithful
database: the `StaleAggregate` race that two sessions produce for free cannot happen between two
callers in one process. Returning a copy would close the last divergence between the double and
the engine — and would change `save`'s identity check, which currently skips the version
comparison when it was handed back the very object it holds.

My read: worth doing, and not while you are away.

### 3d. One inconsistency I did not touch

`ChangeTrackingRepositoryMixin` is twenty-nine characters and, since the change tracking moved
to the flush, it only describes *a store with no flush to ask*. The name no longer says what it
is. Renaming it is the same class of decision as §3a.

---

## What I did not do, on purpose

- **No commits.** 100+ files are staged for you.
- **No changes to `ioc.py` or the bus wiring.** I added a check there earlier in the session and
  removed it when you pointed out it was not needed — the framework already refused that case.
- **No renames.** Every one of them is a direction change and is listed above instead.
