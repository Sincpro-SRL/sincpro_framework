"""The transports, the caller's side — each a `domain.transport.Transport`, chosen by the scheme
of the address a context is hosted at (`services.transports.transport_for`).

    GrpcTransport(HostedAt(Scheme.GRPC, "10.0.0.5:50051")).execute("billing", dto, {})  → Payload

Context: `http` is the standard library's; `grpc` is imported only when an address names it, so
the core needs no extra.
"""
