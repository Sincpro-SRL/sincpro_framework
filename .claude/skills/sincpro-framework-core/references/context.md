# Context

The context is one tree of nodes per process — ROOT → BUS → ENTRYPOINT → APPLICATION / FEATURE →
HOOK, and SCOPE anywhere — read from anywhere; the nearest node wins. This page is the short version;
the component itself (levels, providers, schemas, stores, propagation) is the
`sincpro-framework-context` skill. Long-form guide in the framework repository:
`docs/core/context-manager.md`.

```python
from sincpro_framework.context import use_context

app = UseFramework("my-service")

with app.context({"correlation_id": "123", "user_id": "admin", "tenant_id": "acme"}):
    result = app(some_dto)


class MyFeature(Feature):
    def execute(self, dto):
        correlation_id = self.context.get("correlation_id", "unknown")
        tenant_id = use_context()["tenant_id"]       # the same object, from anywhere
        ...
```

- **One block, many calls.** Every call inside the same `with` block shares its context.
- **Nested contexts** override and inherit: an inner context changes the keys it names and keeps the
  rest.
- **Across buses.** Buses in one process share the tree. A bus called from inside another's
  execution (`self.common(...)`, a subscriber reached through a `SyncQueue`) or inside another bus's
  `with` block runs under the caller's context, adds its own (its keys win), and nothing it adds
  flows back.
- **Read from anywhere:** `use_context()`, `self.context` and `bus.current_context()` are the same
  object, a `dict`; `bus.current_context()` is read-only. A callable stored at wiring time answers
  correctly for every later request (`AuditedMixin` stamping `created_by` reads it this way).
- **Two writes from a handler.** `self.context["key"] = value` lands on the scope of the call:
  siblings see it, it stays for the rest of the enclosing `with` block and goes on the signals
  recorded after it. `self.context.set("key", value)` writes only the current node — what it runs
  afterwards. Without an enclosing block the call has its own scope, gone when it returns.
- **Standard keys:** `user_id`, `tenant_id`, `tenant_ids`. Signals keep their names (`tenant`,
  `user_id`).
- **On every signal.** Each context key goes on the bus's log lines, spans and error events.
  `UseFramework(name, hide_in_logs=["TOKEN"])` keeps a key off all of them; `metric_labels=[...]`
  puts a key on metric series.
- **Typed.** `class MyFeature(Feature[Command, Response, MyContext])` with
  `class MyContext(TypedDict, total=False)` types `self.context` for the IDE.

## Threads and async

**Current limitation:** a handler's `self.context["key"] = value` without an enclosing `with`
block, run through `get_async_bus()`, survives into later async calls. Open a `bus.context(...)`
around async fan-out. A secret goes in as `Secret(value)` (masked on every signal, never travels).

A bare `ThreadPoolExecutor.submit`, `loop.run_in_executor` or `threading.Thread` hands a worker
**no** context on the default (GIL) build, with no error. Use the helpers, which copy the
submitter's context once per task:

```python
from sincpro_framework.context import ContextExecutor, ContextThread, in_context

with ContextExecutor(max_workers=3) as pool:
    results = list(pool.map(lambda item: self.feature_bus.execute(item), dto.items))

ContextThread(target=refresh).start()
existing_pool.submit(in_context(refresh), arg)
```

`bus.thread_context().execute` still works (one per task) for existing code.

- **Async fan-out:** `get_async_bus()` (on `UseFramework` and every bus) returns a stateless
  `AsyncBus` that runs each call via `asyncio.to_thread`, which propagates context itself. Get it
  **once** and `await`/`gather` many; each call gets its own snapshot.

```python
async_bus = framework.get_async_bus()
result_a, result_b = await asyncio.gather(async_bus(dto_a), async_bus(dto_b))
```

- Cancelling the awaiting coroutine does **not** stop the Feature already running in its worker
  thread (Python cannot kill a thread): design for idempotency, not for cancellation stopping work.
- For fan-out with partial-failure handling prefer `asyncio.TaskGroup` over `gather`.

## A caller that is not a bus

A cron or worker opens the flow for the buses it calls with
`from sincpro_framework.context import carrying` — `with carrying(mapping, kind):` opens an
ENTRYPOINT every bus executed inside the block starts from. The cron registry already does this for
each tick.

## Global scope

`framework.context(..., global_scope=True)` writes the bus's own node (BUS level): every execution
of that bus, on every thread, sees the keys while the block is open, and they are restored when it
closes. It is for process-wide values set once (a worker's identity), never for request data.
Process defaults go on ROOT: `use_context().root.set(key, value)`.
