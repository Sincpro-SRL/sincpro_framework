"""Observability is optional, and there is no way for it to take the bus down.

Someone who never installed the extras, or installed them and configured nothing, or
configured a collector that is unreachable, must get exactly the behaviour they would
get from a framework with no observability at all. These run the whole public surface
under each of those conditions.

The "not installed" cases run in a subprocess: blocking an import is process-global.
"""

import subprocess
import sys
from pathlib import Path

import pytest

FRAMEWORK_ROOT = Path(__file__).resolve().parents[2]

BLOCK_BOTH_EXTRAS = """
import sys

for name in (
    "sentry_sdk",
    "opentelemetry",
    "opentelemetry.trace",
    "opentelemetry.context",
    "opentelemetry.sdk",
    "opentelemetry.sdk.resources",
    "opentelemetry.sdk.trace",
    "opentelemetry.sdk.trace.export",
    "opentelemetry.sdk.trace.sampling",
    "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    "opentelemetry.instrumentation.asgi",
):
    sys.modules[name] = None
"""


def run_bare(body: str) -> subprocess.CompletedProcess:
    """Run a script with both extras unimportable."""
    return subprocess.run(
        [sys.executable, "-c", BLOCK_BOTH_EXTRAS + body],
        cwd=str(FRAMEWORK_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def assert_ran(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Neither extra installed
# ---------------------------------------------------------------------------


def test_the_whole_bus_runs_without_the_extras():
    """Features, app services, context, async fan-out, error handlers."""
    assert_ran(run_bare("""
import asyncio

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    UseFramework,
)

class Child(DataTransferObject):
    value: str

class Parent(DataTransferObject):
    value: str

class Boom(DataTransferObject):
    pass

fw = UseFramework("bare-client", log_after_execution=False)

@fw.feature(Child)
class DoChild(Feature):
    def execute(self, dto: Child):
        return f"child:{self.context.get('user_id', '-')}:{dto.value}"

@fw.app_service(Parent)
class DoParent(ApplicationService):
    def execute(self, dto: Parent):
        return self.feature_bus.execute(Child(value=dto.value))

@fw.feature(Boom)
class DoBoom(Feature):
    def execute(self, dto: Boom):
        raise RuntimeError("negocio")

assert fw(Child(value="x")) == "child:-:x"
assert fw(Parent(value="y")) == "child:-:y"

with fw.context({"user_id": "u-1"}) as scoped:
    assert scoped(Child(value="z")) == "child:u-1:z"

async def fan_out():
    bus = fw.get_async_bus()
    return await asyncio.gather(*[bus(Child(value=str(i))) for i in range(3)])

assert asyncio.run(fan_out()) == ["child:-:0", "child:-:1", "child:-:2"]

try:
    fw(Boom())
    raise AssertionError("the business error must still propagate")
except RuntimeError as error:
    assert str(error) == "negocio"

fw.add_feature_error_handler(lambda error: "handled")
assert fw(Boom()) == "handled"

print("ok")
"""))


def test_the_tracing_api_runs_without_the_extras():
    """`with_trace` and `with_parent_trace` still correlate logs with UUIDs."""
    assert_ran(run_bare("""
import warnings

from sincpro_framework import DataTransferObject, Feature, UseFramework

class Ping(DataTransferObject):
    pass

fw = UseFramework("bare-client", log_after_execution=False)

@fw.feature(Ping)
class DoPing(Feature):
    def execute(self, dto: Ping):
        return self.context.get("trace_id", "")

with fw.with_trace() as traced:
    assert traced(Ping())            # a UUID, not an OTel id

with fw.with_trace(trace_id="t-1", span_id="s-1") as traced:
    assert traced(Ping()) == "t-1"

with fw.with_parent_trace() as traced:
    assert traced(Ping())

# A carrier without opentelemetry warns and falls back; it must not raise.
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    with fw.with_trace(carrier={"traceparent": "00-abc-def-01"}) as traced:
        assert traced(Ping())
assert any(issubclass(w.category, RuntimeWarning) for w in caught)

print("ok")
"""))


def test_the_observability_api_answers_without_the_extras():
    """Both doors are safe to call and report `off`, never raise."""
    assert_ran(run_bare("""
from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.observability import process

class Ping(DataTransferObject):
    pass

fw = UseFramework("bare-client", log_after_execution=False)

@fw.feature(Ping)
class DoPing(Feature):
    def execute(self, dto: Ping):
        return "pong"

fw.ignore_sentry_exceptions(ValueError)
fw.build_root_bus()

status = fw.observability.status.model_dump()
assert status["otel"]["state"] == "off", status
assert status["sentry"]["state"] == "off", status
assert fw.observability.identity.bus == "bare-client"

# The transport door, on a process with no opentelemetry at all.
assert process.tracer("asgi") is None
assert process.trace_ids() == {}
assert process.status.active is False
assert process.identity is not None
process.bind_logger(fw.logger)
process.record_error(RuntimeError("500"), layer="asgi")

assert fw(Ping()) == "pong"
print("ok")
"""))


# ---------------------------------------------------------------------------
# Extras installed, nothing configured — the everyday case
# ---------------------------------------------------------------------------


def test_nothing_configured_reports_off_and_still_executes(monkeypatch):
    from sincpro_framework import DataTransferObject, Feature, UseFramework
    from sincpro_framework.sincpro_conf import settings

    monkeypatch.setattr(settings, "otlp_endpoint", None)
    monkeypatch.setattr(settings, "sentry_dsn", None)

    class Ping(DataTransferObject):
        pass

    framework = UseFramework("nothing-configured", log_after_execution=False)

    @framework.feature(Ping)
    class DoPing(Feature):
        def execute(self, dto: Ping) -> str:
            return "pong"

    assert framework(Ping()) == "pong"
    assert framework.observability.status.sentry.state == "off"


# ---------------------------------------------------------------------------
# Configured, but the collector is not there
# ---------------------------------------------------------------------------


def test_an_unreachable_collector_neither_blocks_nor_raises(monkeypatch):
    """The queue absorbs and the export fails on a background thread.

    Measured at ~0.1ms per execution against a closed port; the assertion below is
    loose on purpose, it only has to catch a synchronous export.
    """
    import logging
    import time

    pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")

    # The failed export is the point of the test; its retry log is not.
    for name in ("opentelemetry.exporter.otlp.proto.grpc.exporter", "opentelemetry"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.CRITICAL)
        monkeypatch.setattr(logging.getLogger(name), "disabled", True)

    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from sincpro_framework import DataTransferObject, Feature, UseFramework
    from sincpro_framework.observability import registry

    provider = TracerProvider()
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint="http://127.0.0.1:1"))
    )
    registry.register_tracer_provider("dead-collector", provider)

    class Ping(DataTransferObject):
        pass

    framework = UseFramework("dead-collector", log_after_execution=False)

    @framework.feature(Ping)
    class DoPing(Feature):
        def execute(self, dto: Ping) -> str:
            return "pong"

    framework.build_root_bus()
    started = time.perf_counter()
    results = [framework(Ping()) for _ in range(200)]
    elapsed = time.perf_counter() - started

    assert all(result == "pong" for result in results)
    assert elapsed < 5, f"{elapsed:.1f}s for 200 executions — the export is blocking"
    registry.reset()
