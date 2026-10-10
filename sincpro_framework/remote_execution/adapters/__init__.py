"""The transports, the caller's side — chosen by the scheme of the address a context is hosted at.

    transport_for(HostedAt(Wire.GRPC, "10.0.0.5:50051")).execute("billing", dto, {})  → Payload

Context: one transport per address, shared by every context and thread that reaches it. `http` is
the standard library's; `grpc` is imported only when an address names it, so the core needs no
extra.
"""
