# Liveness and readiness: checks a service registers, every wire's probes

A service answers two questions, and an orchestrator acts on each differently:

| Probe | Asks | Fails when | The orchestrator |
|---|---|---|---|
| **liveness** — `GET /healthz` | can the process answer at all? | a bus is torn down | restarts it |
| **readiness** — `GET /readyz` | can it answer a call now? | a check does not pass — a binary missing, a store down, a key not set | takes it out of the balancer until it passes, nothing restarted |

Every gateway serves both by default. Readiness is the gateway's `readiness`: checks by name, each
a plain callable, each bounded in time. `buses` — every bus still built — is the first; a project
adds what its use cases need.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Checks by name

```python
import shutil
from datetime import timedelta

from starlette.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.entrypoints import FastApiGateway


class CommandScan(DataTransferObject):
    pages: int


class ResponseScan(DataTransferObject):
    pages: int


ocr = UseFramework("ocr", log_after_execution=False)


@ocr.feature(CommandScan)
class Scan(Feature):
    def execute(self, dto: CommandScan) -> ResponseScan:
        return ResponseScan(pages=dto.pages)


ocr.build_root_bus()
settings = {"OCR_LICENSE": "key-of-the-tenant"}


def store_answers() -> None:
    raise ConnectionError("valkey:6379 refused the connection")


gateway = FastApiGateway([ocr], unguarded=True)
gateway.readiness.add("python", lambda: shutil.which("python3") is not None)   # a binary
gateway.readiness.add("license", lambda: bool(settings.get("OCR_LICENSE")))   # a key
gateway.readiness.add("valkey", store_answers, timeout=timedelta(milliseconds=300))   # a store

report = gateway.readiness.report()
assert [(one.name, one.ready) for one in report.checks] == [
    ("buses", True), ("python", True), ("license", True), ("valkey", False),
]
assert report.failing()[0].detail == "ConnectionError: valkey:6379 refused the connection"
```

- **A check is ready** unless it raises — its type and message are the detail — or answers `False`;
  anything else it answers is ready. `ping` methods that raise when the store is down, `bool`
  answers, a function of the project's: all read the same.
- **Each is bounded in time**: `timeout=` per check, one second by default (`ReadinessChecks(timeout=)`
  for all). Every check runs at once, each in a thread of its own, so a probe takes the slowest
  timeout, never their sum. A check that does not answer is not ready — `"did not answer within
  0.3s"` — and keeps running; the next probe waits on that same run instead of starting another, so a
  check that hangs holds one thread, not one per probe.
- **`add` again** under a name replaces the check; **`remove(name)`** stops it — `buses` too, for a
  host that judges its buses its own way.

## What a probe reads

```python
client = TestClient(gateway.app())

assert client.get("/healthz").status_code == 200       # alive: nothing to restart
not_ready = client.get("/readyz")
assert not_ready.status_code == 503
said = not_ready.json()
assert (said["kind"], said["next_step"]) == ("unavailable", "retry_later")
assert said["detail"] == "not ready: valkey (ConnectionError: valkey:6379 refused the connection)"
assert [one["name"] for one in said["checks"] if not one["ready"]] == ["valkey"]

gateway.readiness.remove("valkey")
ready = client.get("/readyz").json()
assert ready["ready"] and len(ready["checks"]) == 3
```

Ready is `200` and every check — `name`, `ready`, `seconds`. Not ready is `503` and the `Problem`
every wire tells (`NotReady`, `unavailable`, `retry_later`): the failing checks in `detail`, every
check beside it in `checks`.

## On every wire

| Wire | Liveness | Readiness |
|---|---|---|
| FastAPI — `FastApiGateway` | `GET /healthz` | `GET /readyz`, `application/problem+json` when it fails |
| REST on Starlette — `RestGateway` | `GET /healthz` | `GET /readyz` |
| JSON-RPC — `RpcGateway` | `GET /healthz` | `GET /readyz` |
| gRPC — `GrpcGateway` | `grpc.health.v1` — the default `health_check` *is* readiness: `NOT_SERVING` while a check fails, and on drain | |

`health_path=` and `ready_path=` move either route, `None` drops it, on `.routes()` and `.app()`.
The routes serve `gateway.readiness.answer` — `(status, body)` — which a host of its own serves at
any path; `gateway.readiness.report()` is the report itself, for a startup check before serving.

**One process, several wires**: each gateway has its readiness; one assigned to another shares it —

```python
from sincpro_framework.entrypoints import RpcGateway

rpc = RpcGateway({"ocr": ocr})
rpc.readiness = gateway.readiness
assert TestClient(rpc.app()).get("/readyz").status_code == 200
```

## Kubernetes

```yaml
livenessProbe:  {httpGet: {path: /healthz, port: 8080}, periodSeconds: 10}
readinessProbe: {httpGet: {path: /readyz,  port: 8080}, periodSeconds: 5, timeoutSeconds: 2}
```

`timeoutSeconds` above the slowest check's timeout: the probe answers once every check has, or has
run out of its time.

## Making it yours

| To change | How |
|---|---|
| What a check is | any callable — a store's `ping`, a function, a lambda |
| How long each waits | `add(name, check, timeout=)`, or `ReadinessChecks(timeout=)` as the default |
| Whether the buses count | `gateway.readiness.remove("buses")` |
| The checks themselves | `gateway.readiness = ReadinessChecks(...)` — one of its own, or another gateway's |
| How readiness is answered | serve `gateway.readiness.report()` from a route of the host's own |
