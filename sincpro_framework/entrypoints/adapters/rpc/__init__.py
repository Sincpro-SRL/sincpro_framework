"""JSON-RPC 2.0 wire: the buses' declared use cases as methods named
`{namespace}.{operation}`, described by one OpenRPC 1.4 document. Starlette is needed only to
serve (`[rpc]` extra).

    rpc = RpcGateway([billing]).group(billing, version="v2")    # billing.v2.issue_invoice
    uvicorn.run(rpc.app())                                      # POST /rpc, GET /openrpc.json

    reply = dispatch(rpc.build(), raw_body, credentials, headers)  # a route of the project's
    status, headers = http_status(rpc.build(), reply)

Context: `RpcGateway` resolves and serves the surface; `JsonRpcWire` is how names are
derived (subclass it to change them); `dispatch` and `http_status` are the one call path — the
gateway's route runs them, and so does a route a project writes under any other framework.
"""

from sincpro_framework.entrypoints.adapters.rpc.entrypoint import RpcGateway, build_rpc_app
from sincpro_framework.entrypoints.adapters.rpc.wire import (
    JsonRpcWire,
    RpcSurface,
    dispatch,
    http_status,
    operation_name,
)

__all__ = [
    "JsonRpcWire",
    "RpcGateway",
    "RpcSurface",
    "build_rpc_app",
    "dispatch",
    "http_status",
    "operation_name",
]
