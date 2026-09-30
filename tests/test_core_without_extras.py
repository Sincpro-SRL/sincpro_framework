"""The core needs none of the extras: an SDK that only has buses must never need a database.

A fresh interpreter with every optional dependency blocked imports the core, runs a bus, a cron,
a migration, a workflow, a frame, a stored use case, auth — its providers, its guard, its ASGI
middleware — and a context hosted over HTTP, all in memory or on the standard library. One `import sqlalchemy`
reached from the core, and every SDK that uses nothing but a bus starts needing a database
driver — this is the check.
"""

import subprocess
import sys
from pathlib import Path

BLOCKED = (
    "sqlalchemy",
    "alembic",
    "opentelemetry",
    "sentry_sdk",
    "fastmcp",
    "starlette",
    "fastapi",
    "uvicorn",
    "grpc",
    "google.protobuf",
    "duckdb",
    "deltalake",
    "pyarrow",
    "faststream",
    "redis",
    "pymemcache",
    "prometheus_client",
)

PROGRAM = r"""
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

for name in BLOCKED:
    sys.modules[name] = None

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.cron import Cron, CronGateway, Crons, InMemoryRuns, Tick
from sincpro_framework.migrations import ContextMigrations, InMemoryEngine, Migrations
from sincpro_framework.workflows import InMemoryWorkflows, Workflows
from sincpro_framework.data_analysis import DataFrame
from sincpro_framework.runtime_use_cases import BusRegistry, InMemoryUseCases, RuntimeUseCase
from sincpro_framework.testing import ManualClock


class CommandPing(DataTransferObject):
    pass


class ResponsePing(DataTransferObject):
    pong: bool


bus = UseFramework("core-only", log_after_execution=False)


@bus.feature(CommandPing)
class Ping(Feature):
    def execute(self, dto: CommandPing) -> ResponsePing:
        return ResponsePing(pong=True)


from sincpro_framework.observability import of, traces


class CommandStamp(DataTransferObject):
    nit: str


@bus.feature(CommandStamp)
@traces.attributes(of(CommandStamp).nit, namespace="core")
class Stamp(Feature):
    def execute(self, dto: CommandStamp) -> ResponsePing:
        traces.annotate({"core.stamped": True, "core.password": "dropped, never raised"})
        return ResponsePing(pong=True)


assert bus(CommandPing(), ResponsePing).pong
assert bus(CommandStamp(nit="1020304050"), ResponsePing).pong
traces.annotate({"core.outside": 1})  # outside a use case: nothing, and nothing raised

crons =Crons("cron-core-only")
crons.add_dependency("bus", bus)
ran = []


@crons.cron(every=timedelta(minutes=1))
class Beat(Cron):
    def run(self, tick: Tick) -> None:
        ran.append(self.bus(CommandPing(), ResponsePing).pong)


clock = ManualClock(datetime(2026, 9, 26, 1, 59, tzinfo=UTC))
gateway = CronGateway([crons], runs=InMemoryRuns(), clock=clock)
gateway.run()
clock.advance(minutes=1)
gateway.wait()
assert ran == [True]

with tempfile.TemporaryDirectory() as folder:
    context = ContextMigrations("core", Path(folder))
    context.store("main", InMemoryEngine())
    migrations = Migrations([context])
    migrations.revision("core", "main", "first")
    assert [step.message for step in migrations.upgrade()] == ["first"]

workflow = {"name": "ping", "steps": [{"id": "ping", "execute": "CommandPing"}], "output": {"pong": "$steps.ping.pong"}}
assert Workflows(bus, InMemoryWorkflows([workflow])).run("ping", {}).output == {"pong": True}

frame = DataFrame.from_rows([{"id": "a", "pong": True}, {"id": "b", "pong": False}])
assert frame.narrow({"field": "pong", "operator": "=", "value": True}).column("id") == ["a"]
try:
    frame.to_parquet()
except ImportError as error:
    assert "sincpro-framework[data-analysis]" in str(error), error
else:
    raise AssertionError("Parquet without pyarrow")


ECHO = '''
from sincpro_framework import DataTransferObject, Feature

class CommandEcho(DataTransferObject):
    said: str

class Echo(Feature):
    def execute(self, dto: CommandEcho) -> CommandEcho:
        return dto
'''
store = InMemoryUseCases()
store.save(RuntimeUseCase(name="echo", source=ECHO))
registry = BusRegistry(bus, store)
assert registry.execute("sincpro_runtime.core-only.echo.CommandEcho", {"said": "hi"}).said == "hi"

from dataclasses import dataclass as _dataclass
from sincpro_framework.caching import CachePolicy, InMemoryKeyValue, QueryCaching
from sincpro_framework.cron import KeyValueRuns
from sincpro_framework.ddd import Entity, MemoryRepository


@_dataclass
class Note(Entity):
    text: str = ""


class QueryNotes(DataTransferObject):
    pass


class ResponseNotes(DataTransferObject):
    count: int


notes = UseFramework("core-only-cache", log_after_execution=False)
notes.add_dependency("repository", MemoryRepository())
asked: list[int] = []


@notes.feature(QueryNotes)
class CountNotes(Feature):
    def execute(self, dto: QueryNotes) -> ResponseNotes:
        asked.append(1)
        return ResponseNotes(count=self.repository.count(Note).value)


QueryCaching(InMemoryKeyValue()).on(notes, QueryNotes, CachePolicy(ttl=timedelta(minutes=1)))
assert notes(QueryNotes(), ResponseNotes).count == notes(QueryNotes(), ResponseNotes).count == 0
assert asked == [1]
assert KeyValueRuns(InMemoryKeyValue()).claim("core", datetime(2026, 9, 27, tzinfo=UTC))

try:
    import sincpro_framework.events.faststream
except ImportError as error:
    assert "sincpro-framework[faststream]" in str(error), error
else:
    raise AssertionError("the FastStream adapter imported without FastStream")

import asyncio

from sincpro_framework.auth import (
    AccessControl,
    ApiKey,
    ApiKeyProvider,
    Credentials,
    IdentityMiddleware,
    InMemoryApiKeys,
    Permission,
    PermissionDenied,
    ServiceTokenProvider,
    as_identity,
)
from sincpro_framework.testing import AuthProviderContract, granting


class Perm(Permission):
    PING = "core.ping"


keys = InMemoryApiKeys()
secret = keys.issue(ApiKey(subject="apikey:bot", permissions={Perm.PING}))
tokens = ServiceTokenProvider("core", {"k": "shared"})
auth = AccessControl[Perm](providers=[ApiKeyProvider(keys), tokens])
guarded = UseFramework("core-only-auth", log_after_execution=False)


@guarded.feature(CommandPing)
@auth.requires(Perm.PING)
class GuardedPing(Feature):
    def execute(self, dto: CommandPing) -> ResponsePing:
        return ResponsePing(pong=True)


auth.on(guarded)
bot = auth.authenticate(Credentials(transport="http", headers={"x-api-key": secret}))
with as_identity(bot):
    assert guarded(CommandPing(), ResponsePing).pong
    assert auth.authenticate(Credentials(**auth.credentials_for(bot).model_dump())).subject == "apikey:bot"
with granting():
    try:
        guarded(CommandPing(), ResponsePing)
    except PermissionDenied:
        pass
    else:
        raise AssertionError("a guarded use case ran for an identity that lacks its permission")

sent: list[dict] = []


async def asgi_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def collect(message):
    sent.append(message)


scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": [(b"x-api-key", b"forged")]}
asyncio.run(IdentityMiddleware(asgi_app, access=auth)(scope, None, collect))
assert sent[0]["status"] == 401, sent

from sincpro_framework.entrypoints.mcp.auth import token_verifier

try:
    token_verifier(auth)
except ImportError as error:
    assert "sincpro-framework[mcp]" in str(error), error
else:
    raise AssertionError("the FastMCP verifier was built without FastMCP")

from sincpro_framework.ddd.query import Query
from sincpro_framework.entrypoints.rest import RestGateway


class QueryPings(Query):
    pass


published = UseFramework("core-only-rest", log_after_execution=False)


@published.feature(QueryPings)
class ListPings(Feature):
    def execute(self, dto: QueryPings) -> ResponsePing:
        return ResponsePing(pong=True)


rest = RestGateway([published], prefix="/api/v1")
assert [route.methods for route in rest.rest_routes()] == [("GET", "POST")]
assert "/api/v1/core-only-rest/query-pings" in rest.openapi()["paths"]
try:
    rest.app()
except ImportError as error:
    assert "sincpro-framework[rest]" in str(error), error
else:
    raise AssertionError("a REST app was served without Starlette")

from sincpro_framework.remote_execution import ContextUnavailable

hosted = UseFramework("core-only-hosted", log_after_execution=False)


@hosted.feature(CommandPing)
class PingHosted(Feature):
    def execute(self, dto: CommandPing) -> ResponsePing:
        return ResponsePing(pong=True)


hosted.hosted_by("http://127.0.0.1:9?timeout=1")
try:
    hosted(CommandPing(), ResponsePing)
except ContextUnavailable:
    pass
else:
    raise AssertionError("an http address with nobody listening answered")

over_grpc = UseFramework("core-only-grpc", log_after_execution=False)


@over_grpc.feature(CommandPing)
class PingOverGrpc(Feature):
    def execute(self, dto: CommandPing) -> ResponsePing:
        return ResponsePing(pong=True)


over_grpc.hosted_by("grpc://127.0.0.1:9")
try:
    over_grpc(CommandPing(), ResponsePing)
except ImportError as error:
    assert "sincpro-framework[grpc]" in str(error), error
else:
    raise AssertionError("a grpc address answered without grpc installed")

# --- PRD_13/14/15: caching, idempotency, declared exposure, the shared failures — all core ---

from sincpro_framework.caching import (
    IDEMPOTENCY_KEY,
    Cache,
    FailSafe,
    Idempotency,
    KeepPolicy,
    Lru,
    Sliding,
    declares_once,
)
from sincpro_framework.entrypoints import Exposure, Gateway
from sincpro_framework.entrypoints.exposure import grpc, internal, mcp, queue, rest, rpc
from sincpro_framework.transport.failures import FailureKind, failure_kind

kept = Cache(eviction=Lru(max_entries=10))
fresh = KeepPolicy(
    freshness=Sliding(idle_for=timedelta(minutes=1), at_most=timedelta(minutes=5)),
    failure=FailSafe(serve_for=timedelta(minutes=5)),
)
assert kept.get_or_compute("k", lambda: 1, fresh) == kept.get_or_compute("k", lambda: 2, fresh) == 1


class CommandCharge(DataTransferObject):
    amount: int


class ResponseCharge(DataTransferObject):
    receipt: int


class CommandSecret(DataTransferObject):
    pass


charges: list[int] = []
idempotency = Idempotency(InMemoryKeyValue())
exposed = UseFramework("core-only-exposed", log_after_execution=False)


@exposed.feature(CommandCharge)
@idempotency.once(expires_after=timedelta(minutes=1))
@rest.post("/charges", status=201)
@grpc()
@rpc()
@mcp(destructive=True)
@queue.consumes("core.charges", producers=("urn:svc:core",))
class Charge(Feature):
    def execute(self, dto: CommandCharge) -> ResponseCharge:
        charges.append(dto.amount)
        return ResponseCharge(receipt=len(charges))


@exposed.feature(CommandSecret)
@internal
class Secret(Feature):
    def execute(self, dto: CommandSecret) -> ResponsePing:
        return ResponsePing(pong=True)


for key in ("k-1", "k-1"):
    with exposed.context({IDEMPOTENCY_KEY: key}):
        exposed(CommandCharge(amount=5), ResponseCharge)
assert charges == [5] and declares_once(Charge)


class RestSurface(Gateway):
    wire = "rest"


surface = RestSurface([exposed], unguarded=True)
assert [entry.command.rsplit(".", 1)[-1] for entry in surface.manifest()] == ["CommandCharge"]
catalog = RestSurface([exposed], exposure=Exposure.CATALOG, unguarded=True)
assert "CommandSecret" not in str(catalog.manifest())
assert failure_kind(PermissionDenied("a", "b", "c")) == FailureKind.PERMISSION_DENIED

for module, extra in (
    ("sincpro_framework.entrypoints.fastapi", "fastapi"),
    ("sincpro_framework.entrypoints.faststream", "faststream"),
    ("sincpro_framework.transport.grpc", "grpc"),
):
    try:
        __import__(module)
    except ImportError as error:
        assert f"sincpro-framework[{extra}]" in str(error), (module, error)
    else:
        raise AssertionError(f"{module} imported without its extra")

from sincpro_framework.entrypoints.grpc import GrpcGateway
from sincpro_framework.entrypoints.mcp import McpGateway
from sincpro_framework.entrypoints.rpc import RpcGateway

for gateway, serve, extra in (
    (GrpcGateway([exposed], unguarded=True), "server", "grpc"),
    (RpcGateway([exposed], unguarded=True), "app", "rpc"),
    (McpGateway([exposed], unguarded=True), "server", "mcp"),
):
    try:
        getattr(gateway, serve)()
    except ImportError as error:
        assert f"sincpro-framework[{extra}]" in str(error), (type(gateway).__name__, error)
    else:
        raise AssertionError(f"{type(gateway).__name__}.{serve}() served without its extra")

# --- metrics: declared and recorded with no backend installed ---------------------------------

from enum import StrEnum as _StrEnum

from sincpro_framework.observability.metrics import InMemoryRecorder, metrics, of


class Channel(_StrEnum):
    WEB = "web"


class CommandVisit(DataTransferObject):
    channel: Channel


visits = UseFramework("core-only-metrics", log_after_execution=False)


@visits.feature(CommandVisit)
@metrics.counts(by=of(CommandVisit).channel)
class Visit(Feature):
    def execute(self, dto: CommandVisit) -> None:
        return None


visits(CommandVisit(channel=Channel.WEB))  # the default: no recorder, nothing recorded
recorded = InMemoryRecorder()
with metrics.using(recorded):
    visits(CommandVisit(channel=Channel.WEB))
assert recorded.totals("core_only_metrics.visit.runs") == {
    (("channel", "web"), ("service.name", "core-only-metrics")): 1
}

try:
    import sincpro_framework.observability.metrics.adapters.prometheus
except ImportError as error:
    assert "sincpro-framework[prometheus]" in str(error), error
else:
    raise AssertionError("the Prometheus recorder imported without prometheus_client")

from sincpro_framework.observability.metrics.adapters.otel import OtelRecorder

try:
    OtelRecorder()
except ImportError as error:
    assert "sincpro-framework[opentelemetry]" in str(error), error
else:
    raise AssertionError("the OTel recorder was built without OpenTelemetry")

loaded = sorted(name for name in BLOCKED if sys.modules.get(name) is not None)
assert loaded == [], loaded
print("ok")
"""


def test_the_core_runs_with_every_extra_missing():
    program = f"BLOCKED = {BLOCKED!r}\n{PROGRAM}"
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ok")
