"""Without sentry-sdk or opentelemetry, UseFramework still executes."""

import subprocess
import sys
from pathlib import Path


def test_feature_runs_when_otel_and_sentry_are_not_installed():
    """A client that never installed the extras must still get a Feature result."""
    framework_root = Path(__file__).resolve().parents[2]
    script = r"""
import sys

for name in (
    "sentry_sdk",
    "opentelemetry",
    "opentelemetry.trace",
    "opentelemetry.sdk",
    "opentelemetry.sdk.resources",
    "opentelemetry.sdk.trace",
    "opentelemetry.sdk.trace.export",
    "opentelemetry.sdk.trace.sampling",
    "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
):
    sys.modules[name] = None

from sincpro_framework import DataTransferObject, Feature, UseFramework

class Ping(DataTransferObject):
    pass

fw = UseFramework("bare-client", log_after_execution=False)

@fw.feature(Ping)
class DoPing(Feature):
    def execute(self, dto: Ping):
        return "pong"

assert fw(Ping()) == "pong"
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(framework_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout
