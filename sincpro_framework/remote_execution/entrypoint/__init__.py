"""What remote execution exposes — the Open Host Service, a bounded context hosted for other services.

    serve_contexts / Attach / OpenHost     `hosts`: its own deployment, a thread, or a subprocess
    open_host_routes                       `http`: a route on the ASGI app the service already has
    open_host_handler                      `grpc`: what every `GrpcGateway` mounts
    execute_hosted                         `execution`: the execution both run

Context: `hosts` needs no extra until it serves; `http` needs `[rpc]`, `grpc` needs `[grpc]` — each
is imported where it is used.
"""

from sincpro_framework.remote_execution.entrypoint.hosts import (
    Attach,
    OpenHost,
    serve_contexts,
)

__all__ = ["Attach", "OpenHost", "serve_contexts"]
