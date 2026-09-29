"""What remote execution exposes — the Open Host Service, a bounded context hosted for other services.

    serve_contexts / Attach / OpenHost     `hosts`: its own deployment, a thread, or a subprocess
    open_host_routes                       `http`: a route on the ASGI app the service already has
    open_host                              `grpc`: the door on a server of its own, or mounted on one
    execute_hosted                         `execution`: the execution both run

Context: `hosts` needs no extra until it serves; `http` needs `[rpc]`, `grpc` needs `[grpc]` — each
is imported where it is used.
"""

from sincpro_framework.remote_execution.entrypoint.hosts import (
    Attach,
    OpenHost,
    serve_contexts,
)


def open_host(contexts):  # type: ignore[no-untyped-def]
    """The internal door for `contexts` (`entrypoint.grpc.open_host`), imported when used —
    it needs the `[grpc]` extra."""
    from sincpro_framework.remote_execution.entrypoint.grpc import open_host as door

    return door(contexts)


__all__ = ["Attach", "OpenHost", "open_host", "serve_contexts"]
