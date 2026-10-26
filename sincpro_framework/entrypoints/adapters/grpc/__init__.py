"""The gRPC wire: a bus's declared surface as AIP-named unary services over `Struct` — what
`GrpcGateway` builds on.

    GrpcWire().derive(operation, group)     # /billing.v1.BillingService/IssueInvoice

A server of the project's, with hand-written servicers next to the generated ones, takes the
server pieces from `wire` — which needs the `[grpc]` extra:

    from sincpro_framework.entrypoints.adapters.grpc.wire import framework_interceptors

    server = grpc.server(pool, interceptors=framework_interceptors())
    GrpcGateway([billing]).mount(server)              # bus_call in hand-written servicers

Importing this package needs no `[grpc]` extra: the names and the `.proto` export load `grpc` /
`protobuf` only once a server or handlers are asked for (`wire.py`).
"""

from sincpro_framework.entrypoints.adapters.grpc.naming import GrpcWire

__all__ = ["GrpcWire"]
