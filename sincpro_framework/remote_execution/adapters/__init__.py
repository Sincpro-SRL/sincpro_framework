"""The transports, the caller's side — chosen by the scheme of the address a context is hosted at.

    transport_for(HostedAt(Wire.GRPC, "10.0.0.5:50051")).execute("billing", dto, Response, {})

Context: one transport per address, shared by every context and thread that reaches it. `http` is
the standard library's; `grpc` is imported only when an address names it, so the core needs no
extra.
"""

from functools import lru_cache

from sincpro_framework.remote_execution.adapters.transport import Transport
from sincpro_framework.transport.addresses import HostedAt, Wire


@lru_cache(maxsize=None)
def transport_for(hosted_at: HostedAt) -> Transport:
    match hosted_at.wire:
        case Wire.GRPC:
            from sincpro_framework.remote_execution.adapters.grpc import GrpcTransport

            return GrpcTransport(hosted_at)
        case Wire.HTTP | Wire.HTTPS:
            from sincpro_framework.remote_execution.adapters.http import HttpTransport

            return HttpTransport(hosted_at)


__all__ = ["Transport", "transport_for"]
