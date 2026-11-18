# Async threads: where every async door runs the buses

A bus is synchronous: `def execute`, a session, a store. A project never declares anything
async: it holds one `UseFramework` per bounded context and calls it — `billing(dto)` — from sync
code, which runs the bus in its own thread. What is async is **who calls**: a server that serves
every request on one event loop — a REST route, a JSON-RPC body, an MCP tool, a queue's message, a
context hosted for another service, or `async def` code of the project's through
`get_async_bus()`. Such a door cannot run `execute` on its loop without stopping every other
request while it waits, so it hands the call to a thread: one of the bus's **async threads**.

| Who calls the bus | Sync or async | On the async threads |
|---|---|---|
| Odoo, a script, a Feature calling another bus — `billing(dto)` | sync | no: its own thread |
| `CronGateway`, `Process` / `Poll` | sync, a thread per loop | no |
| `GrpcGateway` | sync, the server's own pool (`server(max_workers=…)`) | no |
| `FastApiGateway`, `bus_call(bus)`, `RestGateway`, `RpcGateway` | async | yes, every request |
| `McpGateway` | async | yes, every tool call |
| `QueueGateway` | async | yes, every message |
| a context hosted over HTTP (`open_host_routes`) | async | yes, every call from another service |
| `async def` code of the project's — `await billing.get_async_bus()(dto)` | async | yes, every `await` |

A bus shares the process's async threads — 40 unless `SINCPRO_FRAMEWORK_ASYNC_THREADS` says
otherwise — or has threads of its own when it asks for them: `UseFramework("sandbox",
async_threads=8)`. Each call runs in a copy of its caller's context — `self.context` reads what the caller had set,
and nothing the call sets leaks back — and is told when whoever awaited it stopped waiting.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Calling a bus from async code

`get_async_bus()` is the async door of the bus a project holds. It holds no state: get it once and
await it from as many tasks as wanted.

```python
import asyncio
import threading

from sincpro_framework import DataTransferObject, Feature, UseFramework

billing = UseFramework("billing-threads", log_after_execution=False)


class CommandIssue(DataTransferObject):
    total: int


class Issued(DataTransferObject):
    total: int
    thread: str


@billing.feature(CommandIssue)
class Issue(Feature):
    def execute(self, dto: CommandIssue) -> Issued:
        return Issued(total=dto.total, thread=threading.current_thread().name)


async def issue_three() -> list[Issued]:
    async_bus = billing.get_async_bus()
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(async_bus(CommandIssue(total=n), Issued)) for n in range(3)]
    return [task.result() for task in tasks]


issued = asyncio.run(issue_three())
assert [one.total for one in issued] == [0, 1, 2]
assert all(one.thread.startswith("sincpro-async") for one in issued)
```

The use case stays `def execute`. A Feature declared `async def execute` is refused when it is
registered: everything around it — interceptors, the unit of work, the stores — is synchronous.

## How many at once

A call that waits on I/O — Odoo over XML-RPC, a payment gateway, a cluster's API — holds its thread
the whole wait, so the async threads are how many calls run at once. Past that number the next
calls wait their turn; none is refused. Two hundred calls that each wait 200 ms take 1.1 s on the
forty threads a process has by default, and 0.3 s on two hundred.

| What the project wants | What it writes |
|---|---|
| nothing | nothing: every bus shares the process's 40 |
| more for the whole service | `SINCPRO_FRAMEWORK_ASYNC_THREADS=150` |
| a slow context that never takes the others' threads | `UseFramework("sandbox", async_threads=8)` |
| that number from the project's settings | `UseFramework("sandbox", async_threads=settings.sandbox.threads)` |

The process's number is read when the first call needs the threads, so what a project's settings
hand over at its start is what is used:

```python
from sincpro_framework.sincpro_conf import settings

assert settings.async_threads >= 1      # 40 unless SINCPRO_FRAMEWORK_ASYNC_THREADS says otherwise
```

A project whose shared settings inherit `FrameworkSettings` sets it in its own file as well
(`async_threads: 150`, [Settings](settings.md)).

A bus with threads of its own runs its async calls on them alone: when every one is busy, its next
calls wait, and every other bus goes on answering on the process's.

```python
sandbox = UseFramework("sandbox-threads", log_after_execution=False, async_threads=2)


@sandbox.feature(CommandIssue)
class IssueInTheSandbox(Feature):
    def execute(self, dto: CommandIssue) -> Issued:
        return Issued(total=dto.total, thread=threading.current_thread().name)


in_the_sandbox = asyncio.run(sandbox.get_async_bus()(CommandIssue(total=1), Issued))
in_billing = asyncio.run(billing.get_async_bus()(CommandIssue(total=1), Issued))
assert in_the_sandbox.thread.startswith("sincpro-async-sandbox-threads")
assert not in_billing.thread.startswith("sincpro-async-sandbox-threads")
```

Nothing is started before it is needed: a bus's threads begin with the first async call that
reaches it, and a sync program never starts any. A JSON-RPC batch runs each of its calls on the
threads of the bus that answers it, one after another.

## When the caller stops waiting

A thread cannot be stopped from outside. A call whose caller went away — an agent that cancelled a
tool call, a queue's `time_limit` that passed, a `TaskGroup` sibling that failed, an
`asyncio.timeout` — runs on to its end, and is told: `self.context.cancelled` (or
`use_context().cancelled`) becomes `True`. A use case that works in steps reads it between two of
them and stops itself.

```python
import time

started = threading.Event()
stopped_at: list[int] = []


class CommandExport(DataTransferObject):
    pages: int


@billing.feature(CommandExport)
class Export(Feature):
    def execute(self, dto: CommandExport) -> None:
        started.set()
        for page in range(dto.pages):
            if self.context.cancelled:
                stopped_at.append(page)
                return
            time.sleep(0.01)


async def cancel_the_export() -> None:
    export = asyncio.create_task(billing.get_async_bus()(CommandExport(pages=500)))
    await asyncio.to_thread(started.wait, 2)
    export.cancel()
    try:
        await export
    except asyncio.CancelledError:
        pass


asyncio.run(cancel_the_export())
deadline = time.monotonic() + 3
while not stopped_at and time.monotonic() < deadline:
    time.sleep(0.01)
assert stopped_at and stopped_at[0] < 500
```

Nothing is stopped or rolled back for it: a write half done because nobody waits any more is
worse than a write finished. A call still waiting its turn when its caller left never runs. A call
run with no event loop awaiting it — `billing(dto)` from sync code — is never cancelled. A REST
client that disconnects does not cancel its route: the route answers into the void.

## Extending it

- **Size, not replace.** The threads are the framework's; a project says how many — for the
  process, and for a bus that needs its own.
- **A route of the project's.** `Depends(bus_call(billing))` runs on the same threads as a
  generated route ([FastAPI](../entrypoints/fastapi.md)).
- **A pool the project owns.** For work the project fans out itself, `ContextExecutor` and
  `in_context` hand each task its submitter's context, and `bus.thread_context()` binds a bus to it
  ([Context](context-manager.md)). Such a pool is the project's to size; a call it hands to the
  bus runs there, not on the async threads.
- **Long work.** A use case that should outlive its caller belongs on a queue or a
  [pipeline](pipelines.md), not on an async thread someone awaits.

## Why it is this way

- **One number, said once.** Before, FastAPI and MCP shared anyio's 40 threads, everything else
  took asyncio's `min(32, cpu + 4)`, and neither was the project's to size. The number of threads
  is the number of calls a service serves at once.
- **Shared by default, its own when asked.** Most contexts of a service answer quickly and share
  well; the one that waits minutes on another system is the one that would take every thread. It
  says so where it is declared (`async_threads=`) and waits on its own threads, never on the
  others'. Every door chooses the bus before handing the call over — a JSON-RPC body is read on
  the event loop, each call routed to the threads of the bus that answers it.
- **Only what enters through a door.** A Feature that calls another bus runs it in its own thread,
  synchronously: the threads count calls that come in, not the calls those make.
- **Cancellation is information.** Python cannot stop a thread, and a use case cut halfway through
  a write leaves less consistency than one that finished. The framework says the caller left; the
  use case decides.
- **Use cases stay synchronous.** An `async def execute` would block the event loop on every
  synchronous step around it — an interceptor, a repository, a store — or need two of each.
