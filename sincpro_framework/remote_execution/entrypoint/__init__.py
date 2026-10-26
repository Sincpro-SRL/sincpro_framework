"""What remote execution exposes — the Open Host Service, a bounded context hosted for other services.

    serve_contexts / Attach / OpenHost     `hosts`: its own deployment, a thread, or a subprocess
    open_host_routes                       `http`: a route on the ASGI app the service already has
    open_host                              `grpc`: the door on a server of its own, or mounted on one

Both doors run the same execution, `services.hosted_execution.execute_hosted`.

Context: `hosts` needs no extra until it serves; `http` needs `[rpc]`, `grpc` needs `[grpc]` — each
is imported where it is used.
"""
