# Context

Metadata propagates with `contextvars`, isolated per execution and per bus. Long-form guide in the
framework repository: `docs/core/context-manager.md`.

```python
app = UseFramework("my-service")

with app.context({"correlation_id": "123", "user.id": "admin"}) as app_with_context:
    result = app_with_context(some_dto)


class MyFeature(Feature):
    def execute(self, dto):
        correlation_id = self.context.get("correlation_id", "unknown")
        ...
```

- **One block, many calls.** Every call inside the same `with` block shares its context.
- **Nested contexts** override and inherit: an inner context changes the keys it names and keeps the
  rest.
- **Across buses.** The context of a request follows it into every bus it reaches: a Feature calling
  `self.common(...)`, a subscriber reached through a `SyncQueue` starts from the caller's context,
  adds its own (its keys win), and nothing it adds flows back. Outside an execution nothing is
  inherited: `with sales.context({...}): common(dto)` called directly gives `common` an empty
  context. Open the context on the bus you call.
- **Read from anywhere:** `bus.current_context()` is a read-only, live view: a callable stored at
  wiring time answers correctly for every later request (`AuditedMixin` stamping `created_by` reads
  it this way).
- **Writes from a handler.** `self.context["key"] = value` writes into the open context: it stays for
  the rest of the enclosing `with` block and goes on the signals recorded after it. Without an
  enclosing block the call has its own context, gone when it returns.
- **On every signal.** Each context key goes on the bus's log lines, spans and error events.
  `UseFramework(name, hide_in_logs=["TOKEN"])` keeps a key off all of them; `metric_labels=[...]`
  puts a key on metric series.
- **Typed.** `class MyFeature(Feature[Command, Response, MyContext])` with
  `class MyContext(TypedDict, total=False)` types `self.context` for the IDE.

## Threads and async

`contextvars` is isolated **per OS thread** on a regular (GIL) build. `loop.run_in_executor` /
`executor.submit` do **not** copy the context, so a worker sees an empty one, with no error.

- **Manual `ThreadPoolExecutor`:** `thread_context()` is a method of a built bus (`self.feature_bus`
  inside an ApplicationService, `framework.bus` from outside). It captures the calling thread's
  context and returns a `ThreadContextBus`. Call it **once per task** and submit `.execute`: a
  captured context can be entered by one thread at a time, and sharing one raises `RuntimeError`.

```python
with ThreadPoolExecutor(max_workers=3) as executor:
    futures = [executor.submit(self.feature_bus.thread_context().execute, item) for item in dto.items]
```

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

A cron or worker hands its context to the buses it calls with
`sincpro_framework.context.mixin.carrying(mapping)`: every bus executed inside the block starts
from it. The cron registry already does this for each tick.

## Global scope

`framework.context(..., global_scope=True)` publishes the keys on the bus instance: every execution
of that bus, on every thread, sees them while the block is open, and they are restored when it
closes. It is for process-wide values set once (a worker's identity), never for request data.
