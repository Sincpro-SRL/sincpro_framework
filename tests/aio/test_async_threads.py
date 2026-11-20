"""The async threads: as many calls at once as a bus has threads — its own, or the process's —,
every async door on them, and a call told when whoever awaited it stopped waiting.

Each incident here is one a service meets in production: a slow context that takes every thread, an
agent that cancels a tool call while it runs, a call cancelled before its turn that still ran.
"""

import asyncio
import threading
import time
from collections.abc import Iterator

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework, sincpro_conf
from sincpro_framework.aio import async_threads
from sincpro_framework.settings.domain.config import FrameworkSettings
from sincpro_framework.settings.entrypoint.building import DEFAULT_CONFIG_FILE_PATH
from sincpro_framework.settings.services.building import build_settings


class CommandWait(DataTransferObject):
    seconds: float = 0.05


class CommandPoll(DataTransferObject):
    pass


class CommandProbe(DataTransferObject):
    pass


class Probed(DataTransferObject):
    thread: str
    cancelled: bool


running = 0
peak = 0
counting = threading.Lock()
polled_until_cancelled: list[bool] = []
poll_started = threading.Event()
probes_run: list[str] = []

bus = UseFramework("async-threads", log_after_execution=False)


@bus.feature(CommandWait)
class Wait(Feature):
    def execute(self, dto: CommandWait) -> None:
        global running, peak
        with counting:
            running += 1
            peak = max(peak, running)
        time.sleep(dto.seconds)
        with counting:
            running -= 1


@bus.feature(CommandPoll)
class Poll(Feature):
    def execute(self, dto: CommandPoll) -> None:
        poll_started.set()
        for _ in range(300):
            if self.context.cancelled:
                polled_until_cancelled.append(True)
                return
            time.sleep(0.01)
        polled_until_cancelled.append(False)


@bus.feature(CommandProbe)
class Probe(Feature):
    def execute(self, dto: CommandProbe) -> Probed:
        probes_run.append(threading.current_thread().name)
        return Probed(
            thread=threading.current_thread().name, cancelled=self.context.cancelled
        )


@pytest.fixture
def two_threads(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A pool of its own, two threads — the process's is put back after the test."""
    monkeypatch.setattr(async_threads, "_shared", None)
    monkeypatch.setattr(sincpro_conf.settings, "async_threads", 2)
    yield
    if async_threads._shared is not None:
        async_threads._shared.shutdown(wait=True)


def _eventually(condition, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_no_more_calls_run_at_once_than_there_are_async_threads(two_threads) -> None:
    global peak
    peak = 0
    async_bus = bus.get_async_bus()

    async def six_at_once() -> None:
        async with asyncio.TaskGroup() as group:
            for _ in range(6):
                group.create_task(async_bus(CommandWait()))

    asyncio.run(six_at_once())
    assert peak == 2


def test_a_running_call_reads_that_its_caller_stopped_waiting() -> None:
    polled_until_cancelled.clear()
    poll_started.clear()

    async def cancel_once_it_runs() -> None:
        call = asyncio.create_task(bus.get_async_bus()(CommandPoll()))
        await asyncio.to_thread(poll_started.wait, 3)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

    asyncio.run(cancel_once_it_runs())
    assert _eventually(lambda: bool(polled_until_cancelled))
    assert polled_until_cancelled == [True]


def test_a_call_cancelled_before_its_turn_never_runs(two_threads) -> None:
    probes_run.clear()
    async_bus = bus.get_async_bus()

    async def cancel_the_queued_one() -> None:
        busy = [asyncio.create_task(async_bus(CommandWait(seconds=0.3))) for _ in range(2)]
        await asyncio.sleep(0.05)
        queued = asyncio.create_task(async_bus(CommandProbe()))
        await asyncio.sleep(0.05)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        await asyncio.gather(*busy)

    asyncio.run(cancel_the_queued_one())
    assert probes_run == []


def test_a_call_nobody_awaits_from_a_loop_is_never_cancelled() -> None:
    probed = bus(CommandProbe(), Probed)
    assert probed is not None and not probed.cancelled


def test_the_async_door_runs_on_the_process_async_threads() -> None:
    probed = asyncio.run(bus.get_async_bus()(CommandProbe(), Probed))
    assert probed is not None
    assert probed.thread.startswith("sincpro-async")
    assert not probed.cancelled


def test_the_environment_sizes_the_async_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SINCPRO_FRAMEWORK_ASYNC_THREADS", raising=False)
    assert (
        build_settings(FrameworkSettings, DEFAULT_CONFIG_FILE_PATH, None, None).async_threads
        == 40
    )
    monkeypatch.setenv("SINCPRO_FRAMEWORK_ASYNC_THREADS", "200")
    assert (
        build_settings(FrameworkSettings, DEFAULT_CONFIG_FILE_PATH, None, None).async_threads
        == 200
    )


def test_a_fastapi_route_runs_its_bus_on_its_async_threads() -> None:
    pytest.importorskip("fastapi")
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from sincpro_framework.entrypoints.adapters.fastapi import BusCall, bus_call

    app = FastAPI()

    @app.post("/probe")
    async def probe(call: BusCall = Depends(bus_call(bus))) -> dict[str, str]:
        probed = await call(CommandProbe())
        return {"thread": probed.thread}

    assert TestClient(app).post("/probe").json()["thread"].startswith("sincpro-async")


def test_an_mcp_tool_runs_its_bus_on_its_async_threads() -> None:
    pytest.importorskip("fastmcp")
    from sincpro_framework.entrypoints.adapters.mcp.mcp import tool_function

    tool = tool_function(
        CommandProbe, "probe", "Probe.", lambda payload: bus(CommandProbe(**payload)), bus
    )
    probed = asyncio.run(tool())
    assert probed.thread.startswith("sincpro-async")


sandbox = UseFramework("async-threads-sandbox", log_after_execution=False, async_threads=1)
sandbox_release = threading.Event()


class CommandHold(DataTransferObject):
    pass


@sandbox.feature(CommandHold)
class Hold(Feature):
    def execute(self, dto: CommandHold) -> None:
        sandbox_release.wait(3)


@sandbox.feature(CommandProbe)
class SandboxProbe(Feature):
    def execute(self, dto: CommandProbe) -> Probed:
        return Probed(
            thread=threading.current_thread().name, cancelled=self.context.cancelled
        )


def test_a_bus_with_threads_of_its_own_runs_on_them() -> None:
    probed = asyncio.run(sandbox.get_async_bus()(CommandProbe(), Probed))
    assert probed is not None
    assert probed.thread.startswith("sincpro-async-async-threads-sandbox")


def test_a_bus_that_takes_every_thread_of_its_own_leaves_the_others_answering() -> None:
    """The incident: one slow context took every thread of the service, and calls of the other
    contexts — authentication among them — waited behind it."""
    sandbox_release.clear()

    async def sandbox_full_others_answer() -> Probed | None:
        held = asyncio.create_task(sandbox.get_async_bus()(CommandHold()))
        await asyncio.sleep(0.05)
        try:
            async with asyncio.timeout(1):
                return await bus.get_async_bus()(CommandProbe(), Probed)
        finally:
            sandbox_release.set()
            await held

    probed = asyncio.run(sandbox_full_others_answer())
    assert probed is not None and probed.thread.startswith("sincpro-async")


def test_a_bus_with_no_thread_is_refused_where_it_is_declared() -> None:
    from sincpro_framework import exceptions

    with pytest.raises(exceptions.ProgrammingError, match="async_threads=0"):
        UseFramework("async-threads-none", async_threads=0)


def test_each_call_of_a_json_rpc_batch_runs_on_the_threads_of_the_bus_that_answers_it() -> (
    None
):
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from sincpro_framework.entrypoints import Exposure, RpcGateway

    sandbox_probes = UseFramework(
        "async-threads-rpc-sandbox", log_after_execution=False, async_threads=1
    )

    @sandbox_probes.feature(CommandProbe)
    class _SandboxProbe(Feature):
        def execute(self, dto: CommandProbe) -> Probed:
            return Probed(thread=threading.current_thread().name, cancelled=False)

    shared_probes = UseFramework("async-threads-rpc-shared", log_after_execution=False)

    @shared_probes.feature(CommandProbe)
    class _SharedProbe(Feature):
        def execute(self, dto: CommandProbe) -> Probed:
            return Probed(thread=threading.current_thread().name, cancelled=False)

    gateway = RpcGateway(
        {"sandbox": sandbox_probes, "shared": shared_probes},
        exposure=Exposure.CATALOG,
    )
    methods = sorted(gateway.build().methods)
    batch = [
        {"jsonrpc": "2.0", "id": number, "method": method, "params": {}}
        for number, method in enumerate(methods)
    ]
    replies = TestClient(gateway.app()).post("/rpc", json=batch).json()
    threads = {reply["id"]: reply["result"]["thread"] for reply in replies}
    by_method = {methods[number]: thread for number, thread in threads.items()}
    sandbox_method = next(one for one in methods if one.startswith("sandbox"))
    shared_method = next(one for one in methods if one.startswith("shared"))
    assert by_method[sandbox_method].startswith("sincpro-async-async-threads-rpc-sandbox")
    assert not by_method[shared_method].startswith("sincpro-async-async-threads-rpc-sandbox")
