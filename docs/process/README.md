# One process, the loops it runs

`sincpro_framework.entrypoints.entrypoint.workers` keeps the program alive. It is an entrypoint but not a
wire: the wires put use cases on a protocol, a process runs the loops that call them.

For a deployment, the worker is this OS process. Inside it, each thing that stays up is a
loop: `run` blocks, `stop` makes it return. One process can run several.

```python
from datetime import timedelta

from sincpro_framework.entrypoints.adapters.cron import CronGateway
from sincpro_framework.entrypoints.entrypoint.workers import Poll, Process

def deliver() -> None:
    relay.run_once()

Process(
    CronGateway([billing_crons]),
    Poll(timedelta(seconds=2), deliver),
).run()
```

`Process.run()` handles SIGINT and SIGTERM. A test calls `run(handle_signals=False)` and then
`stop`. One loop that raises is logged. The others keep going until `stop`. `stop` during a
poll tick returns when that tick returns.

## What each word is

| Word | What it is |
|---|---|
| Process | This program, staying up, running one or more loops. `sincpro_framework.entrypoints.entrypoint.workers.Process`. |
| Loop | `run` / `stop`. `CronGateway` is one. `Poll` is one. A project class with those two methods is one. |
| Poll | Every interval, call the function the project passed. The function is the pass. |
| Cron | A clock. `CronGateway` is the loop. `CronProcess` is that loop in a child, beside a server. `workers=` on the gateway is how many ticks run at once, a thread cap. |
| Outbox | The context's event table. `EventRelay.run_once` is one pass. A `Poll` or a cron calls it. The relay has no timer. |
| Broker | `faststream run` is that program. It is not started by `Process`. |
| Server | The HTTP, RPC, gRPC or MCP gateway. Its own program, or the parent of a `CronProcess`. |
| Job | Not a type in this framework. A one-off Command is a Command. A row the product runs later is the product's table. |

`Poll` does not know the table. A pass that claims a row is a function the project writes and
hands to `Poll`. Forge's reclaim, claim and run stay Forge's Commands. The same `Poll` can
call `relay.run_once` instead. Nothing here requires the outbox, a broker, or a cron.
