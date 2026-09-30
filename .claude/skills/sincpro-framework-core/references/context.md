# Context

Depth: `docs/core/context-manager.md`. Metadata propagates with `contextvars`, isolated per
execution.

```python
app = UseFramework("my-service")

with app.context({"correlation_id": "123", "user.id": "admin"}) as app_with_context:
    result = app_with_context(some_dto)


class MyFeature(Feature):
    def execute(self, dto):
        correlation_id = self.context.get("correlation_id", "unknown")
        ...
```

- **Nested contexts** override and inherit: an inner context changes the keys it names and keeps the
  rest.
- **Across buses.** The context of a request follows it into every bus it reaches — a Feature calling
  `self.common(...)`, a subscriber reached through a `SyncQueue` starts from the caller's context,
  adds its own (its keys win), and nothing it adds flows back. Outside an execution nothing is
  inherited.
- **Read from anywhere:** `bus.current_context()` — the context in play, read-only.
- **Error enrichment.** An exception raised inside a context is enriched with the context data on
  `error.context_info` (`context_data`, `timestamp`) for debugging.

## Threads and async — the one trap

`contextvars` is isolated **per OS thread**. `loop.run_in_executor` / `executor.submit` do **not**
copy the context, so a worker sees the (usually empty) shared context.

- **Manual `ThreadPoolExecutor`:** `bus.thread_context()` captures the calling thread's context and
  returns a `ThreadContextBus`. Call it **once per task** and submit `.execute` (a `contextvars.Context`
  can be entered by one thread at a time — sharing one raises `RuntimeError`).

```python
with ThreadPoolExecutor(max_workers=3) as executor:
    futures = [executor.submit(self.feature_bus.thread_context().execute, item) for item in dto.items]
```

- **Async fan-out:** `bus.get_async_bus()` returns a stateless `AsyncBus` that runs each call via
  `asyncio.to_thread` (which propagates context itself). Get it **once** and `await`/`gather` many —
  each call gets its own fresh snapshot.

```python
async_bus = framework.get_async_bus()
result_a, result_b = await asyncio.gather(async_bus(dto_a), async_bus(dto_b))
```

- Cancelling the awaiting coroutine does **not** stop the Feature already running in its worker
  thread (Python cannot kill a thread) — design for idempotency, not for cancellation stopping work.
- For fan-out with partial-failure handling prefer `asyncio.TaskGroup` over `gather`.

## Global scope

`framework.context(..., global_scope=True)` is process-global and leaks across concurrent calls —
use the per-call context manager only.
