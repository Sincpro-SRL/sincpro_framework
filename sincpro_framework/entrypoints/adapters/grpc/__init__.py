"""The gRPC wire: a bus's declared surface as AIP-named unary services over `Struct`.

    from sincpro_framework.entrypoints.adapters.grpc import GrpcGateway

    GrpcGateway([billing]).run("0.0.0.0:50051")     # /billing.v1.BillingService/IssueInvoice
    GrpcGateway([billing]).write_proto_files("protos/")   # stubs for Go / TypeScript clients
    server = grpc.server(pool, interceptors=framework_interceptors())   # a server of mine:
    GrpcGateway([billing]).mount(server)              # bus_call in hand-written servicers

Importing it needs no `[grpc]` extra: the gateway, the names and the `.proto` export load
`grpc` / `protobuf` only once a server or handlers are asked for (`wire.py`).
"""

from sincpro_framework.entrypoints.adapters.grpc.entrypoint import (
    GrpcGateway,
    build_grpc_server,
    bus_call,
    framework_interceptors,
)
from sincpro_framework.entrypoints.adapters.grpc.naming import GrpcWire

__all__ = [
    "GrpcGateway",
    "GrpcWire",
    "build_grpc_server",
    "bus_call",
    "framework_interceptors",
]
