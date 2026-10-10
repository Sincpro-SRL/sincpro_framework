"""Hosting a context in this process for a test — over gRPC (`serve(..., Attach.THREAD)`) or HTTP
(the open host route on an app of its own) — and the address a caller dials."""

import socket
import threading
import time
from collections.abc import Iterator

import uvicorn
from starlette.applications import Starlette

from sincpro_framework import UseFramework
from sincpro_framework.remote_execution import Attach, serve_contexts
from sincpro_framework.remote_execution.entrypoint.http import (
    open_host_routes,
)

TRANSPORTS = ("grpc", "http")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def host_over(transport: str, contexts: list[UseFramework]) -> Iterator[str]:
    """Host `contexts` over `transport` in this process; the base address a caller dials."""
    if transport == "grpc":
        host = serve_contexts(contexts, "127.0.0.1:0", Attach.THREAD)
        try:
            yield f"grpc://{host.address}"
        finally:
            host.stop(0)
        return
    port = _free_port()
    config = uvicorn.Config(
        Starlette(routes=open_host_routes(contexts)),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)
