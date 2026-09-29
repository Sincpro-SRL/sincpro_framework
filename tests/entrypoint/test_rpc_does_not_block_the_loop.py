"""The JSON-RPC endpoint runs the bus off the event loop: a slow use case does not stall every
other request, `/healthz` included."""

import asyncio
import threading

import httpx

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.entrypoints.exposure import rpc
from sincpro_framework.entrypoints.rpc import RpcGateway


class CommandSlow(DataTransferObject):
    pass


def test_health_answers_while_a_slow_rpc_call_is_still_running():
    release = threading.Event()
    bus = UseFramework("slow", log_after_execution=False)

    @bus.feature(CommandSlow)
    @rpc()
    class Slow(Feature):
        def execute(self, dto: CommandSlow) -> None:
            release.wait(5)

    app = RpcGateway({"svc": bus}, unguarded=True).app()

    async def scenario() -> tuple[int, bool]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            call = asyncio.create_task(
                client.post(
                    "/rpc",
                    json={"jsonrpc": "2.0", "id": 1, "method": "svc.slow"},
                )
            )
            await asyncio.sleep(0.1)
            health = await asyncio.wait_for(client.get("/healthz"), timeout=2)
            still_running = not call.done()
            release.set()
            await call
            return health.status_code, still_running

    status, still_running = asyncio.run(scenario())

    assert status == 200 and still_running
