"""The core needs none of the extras: an SDK that only has buses must never need a database.

A fresh interpreter with every optional dependency blocked imports the core, runs a bus, a cron,
a migration, a workflow, a frame and a stored use case, all in memory. One `import sqlalchemy`
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
    "uvicorn",
    "grpc",
    "duckdb",
    "deltalake",
    "pyarrow",
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


assert bus(CommandPing(), ResponsePing).pong

crons = Crons("cron-core-only")
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
store.save(RuntimeUseCase("echo", ECHO))
registry = BusRegistry(bus, store)
assert registry.execute("sincpro_runtime.core-only.echo.CommandEcho", {"said": "hi"}).said == "hi"

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
