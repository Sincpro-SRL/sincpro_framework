# Framework audit — findings, fixes, and what needs a decision

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
