"""The Open Host Service: a bounded context hosted for other services however this process runs.

`THREAD` beside the rest of the process — a REST API's lifespan; `PROCESS` in a subprocess of its
own, tied to this one. Each answers an `OpenHost` whose `stop()` ends it.
"""

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.remote_execution import (
    Attach,
    ContextUnavailable,
    serve_contexts,
)

HOSTED = """
import os

from sincpro_framework import DataTransferObject, Feature, UseFramework


class CommandWhere(DataTransferObject):
    pass


class ResponseWhere(DataTransferObject):
    pid: int
    context: str


billing = UseFramework("hosts-billing", log_after_execution=False)
catalog = UseFramework("hosts-catalog", log_after_execution=False)


@billing.feature(CommandWhere)
class BillingWhere(Feature):
    def execute(self, dto: CommandWhere) -> ResponseWhere:
        return ResponseWhere(pid=os.getpid(), context="billing")


@catalog.feature(CommandWhere)
class CatalogWhere(Feature):
    def execute(self, dto: CommandWhere) -> ResponseWhere:
        return ResponseWhere(pid=os.getpid(), context="catalog")
"""


class CommandPing(DataTransferObject):
    pass


class ResponsePing(DataTransferObject):
    pid: int


def _ping(name: str) -> UseFramework:
    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(CommandPing)
    class Ping(Feature):
        def execute(self, dto: CommandPing) -> ResponsePing:
            return ResponsePing(pid=os.getpid())

    return bus


@pytest.fixture
def hosted_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """A context module on disk, imported here and importable by a fresh process."""
    import importlib

    (tmp_path / "hosts_contexts.py").write_text(HOSTED)
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module("hosts_contexts")
    yield module
    sys.modules.pop("hosts_contexts", None)


def test_a_thread_host_answers_until_it_is_stopped():
    host = _ping("hosts-thread").serve("127.0.0.1:0", Attach.THREAD)
    caller = _ping("hosts-thread")
    caller.hosted_by(f"grpc://{host.address}?timeout=5")

    assert caller(CommandPing(), ResponsePing).pid == os.getpid()

    host.stop(0)
    with pytest.raises(ContextUnavailable):
        caller(CommandPing(), ResponsePing)


def test_one_host_serves_several_contexts():
    host = serve_contexts(
        [_ping("hosts-one"), _ping("hosts-two")], "127.0.0.1:0", Attach.THREAD
    )
    try:
        for name in ("hosts-one", "hosts-two"):
            caller = _ping(name)
            caller.hosted_by(f"grpc://{host.address}")
            assert caller(CommandPing(), ResponsePing).pid == os.getpid()
    finally:
        host.stop(0)


def test_a_process_host_answers_from_its_own_process_by_import_path(hosted_module):
    host = serve_contexts(
        ["hosts_contexts:billing", "hosts_contexts:catalog"], "127.0.0.1:0", Attach.PROCESS
    )
    assert host.pid != os.getpid()
    try:
        for name in ("billing", "catalog"):
            caller: UseFramework = getattr(hosted_module, name)
            caller.hosted_by(f"grpc://{host.address}?timeout=10")
            where = caller(hosted_module.CommandWhere(), hosted_module.ResponseWhere)
            assert where.pid != os.getpid() and where.context == name
    finally:
        host.stop(1)

    with pytest.raises(ContextUnavailable):
        hosted_module.billing(hosted_module.CommandWhere(), hosted_module.ResponseWhere)


def test_a_bus_hosted_in_a_process_is_found_by_the_module_that_holds_it(hosted_module):
    host = hosted_module.billing.serve("127.0.0.1:0", Attach.PROCESS)
    try:
        hosted_module.billing.hosted_by(f"grpc://{host.address}?timeout=10")
        where = hosted_module.billing(
            hosted_module.CommandWhere(), hosted_module.ResponseWhere
        )
        assert where.pid != os.getpid()
    finally:
        host.stop(1)


def test_a_process_host_that_cannot_start_says_why():
    with pytest.raises(RuntimeError, match="could not start: ModuleNotFoundError"):
        serve_contexts(["nowhere_to_be_found:billing"], "127.0.0.1:0", Attach.PROCESS)


def test_a_bus_no_module_holds_cannot_be_hosted_in_a_process():
    with pytest.raises(ValueError, match="module:attribute"):
        _ping("hosts-orphan").serve("127.0.0.1:0", Attach.PROCESS)


PARENT = """
import json
import time

from sincpro_framework.remote_execution import Attach, serve_contexts

host = serve_contexts(["hosts_contexts:billing"], "127.0.0.1:0", Attach.PROCESS)
print(json.dumps({"child": host.pid}), flush=True)
time.sleep(60)
"""


def test_a_process_host_stops_when_its_parent_dies(tmp_path: Path, hosted_module):
    (tmp_path / "parent.py").write_text(PARENT)
    parent = subprocess.Popen(
        [sys.executable, str(tmp_path / "parent.py")],
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        stdout=subprocess.PIPE,
        text=True,
    )
    assert parent.stdout is not None
    said = next(line for line in parent.stdout if line.startswith("{"))
    child = json.loads(said)["child"]

    parent.kill()
    parent.wait(10)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and _alive(child):
        time.sleep(0.2)

    assert not _alive(child)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
