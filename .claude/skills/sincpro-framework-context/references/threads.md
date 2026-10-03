# Threads, pools and async

| Runs work in | Gets the caller's context |
|---|---|
| an `asyncio` task, `asyncio.to_thread`, `bus.get_async_bus()` | yes, by itself |
| `ContextExecutor` (a `ThreadPoolExecutor`) | yes — each task a copy of its submitter's |
| `ContextThread` (a `threading.Thread`) | yes — where its creator stood |
| `in_context(fn)` submitted to any pool | yes — the context it was wrapped in |
| `bus.thread_context().execute` | yes — one per task (kept for existing code) |
| a bare `ThreadPoolExecutor.submit`, `loop.run_in_executor`, `threading.Thread` | **no** on the default build; yes on the free-threaded 3.14 build — never rely on it |

```python
from sincpro_framework.context import ContextExecutor, ContextThread, in_context, use_context

with ContextExecutor(max_workers=8) as pool:
    results = list(pool.map(lambda item: siat(CommandSync(item=item)), items))

ContextThread(target=refresh_cufd).start()

existing_pool.submit(in_context(refresh_cufd), nit_id)
```

A copied `contextvars.Context` is entered by one thread at a time — the helpers copy once per task.
Every worker sees the same nodes; nodes are copy-on-write, so a worker never sees a write half done,
and what a worker `set`s stays in its own execution's node.

Processes do not share a tree: across processes the context **travels** (headers, a message) or is
**kept** in a store (`stores-and-propagation.md`).
