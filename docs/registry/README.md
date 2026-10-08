# Registry of one process

A service with several bounded contexts builds one `UseFramework` each. The entrypoint that
serves them — a gateway, a subscriber, `serve()`, a CLI — needs the list without `common/`
importing its siblings and without writing that list a second time.

```python
from sincpro_framework.registry import registry

# in each context, once the instance exists and before its services are imported
registry.add(billing)

# in the entrypoint, after importing the context packages (that import is what builds them)
from sincpro_framework.event_driven import Subscriber

Subscriber(*registry.all())
```

`registry.all()` is this process, in the order the contexts were added. Creating a
`UseFramework` does not add it. Adding the same object again does nothing. A different bus
under a name already held raises `BusAlreadyRegistered`. `fresh()` is a new generation and
is not the registered bus until the project adds it.

The registry is a list. It does not import contexts, does not sort them, and does not look
for who depends on whom. The entrypoint's imports are the build order. A cycle between
contexts is already refused by the architecture check.

Another replica builds its own registry. A context hosted by another service is not held
here, even when its package calls `registry.add`: the caller keeps the bus it imported, and
the context map says the call goes elsewhere. `run_here` is this process serving it, so that
bus stays. A bus pointed elsewhere after it was added leaves on the next read.

## Callers the registry does not replace

| Name | Where it lives | What it does |
|---|---|---|
| Entrypoint | `entrypoints/`, or the project's CLI | The door of the process. REST, JSON-RPC, gRPC, MCP, a broker consumer, a cron process, and a CLI loop are entrypoints. Each one calls Commands. |
| Cron | `cron/` | A clock. At a time, it runs a Command and the tick ends. `CronProcess` is that clock in its own process. |
| Event queue | `event_driven/`, `entrypoints/faststream/` | Carries a fact that already happened to the buses that subscribed. `SyncQueue` runs them inside `publish`. A broker consumer is an entrypoint. |
| Process | `process.py` | The OS process that stays up and runs one or more loops (`run` / `stop`). `Poll` is the loop that calls a function every interval. The function that claims a row is the project's. See `docs/process/README.md`. |
