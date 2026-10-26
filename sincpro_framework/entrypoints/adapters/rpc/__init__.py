"""JSON-RPC 2.0 wire: methods named `{namespace}.{operation}`, described by one OpenRPC 1.4
document, served on Starlette (`[rpc]` extra) — what `RpcGateway` builds on.

    reply = dispatch(rpc.build(), raw_body, credentials, headers)  # a route of the project's
    status, headers = http_status(rpc.build(), reply)

Context: `JsonRpcWire` is how names are derived (subclass it to change them); `dispatch` and
`http_status` are the one call path — the gateway's route runs them, and so does a route a
project writes under any other framework, with `is_json_media_type` for the 415 check.
"""

from sincpro_framework.entrypoints.adapters.rpc.serving import is_json_media_type
from sincpro_framework.entrypoints.adapters.rpc.wire import (
    JsonRpcWire,
    RpcSurface,
    dispatch,
    http_status,
    operation_name,
)

__all__ = [
    "JsonRpcWire",
    "RpcSurface",
    "dispatch",
    "http_status",
    "is_json_media_type",
    "operation_name",
]
