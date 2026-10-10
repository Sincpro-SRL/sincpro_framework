"""Remote execution: a bounded context executed by another service that runs the same code.

    billing.hosted_at("grpc://billing:50051")      the caller: this bus sends billing's DTOs there
    serve_contexts([billing])                       the host: billing answers other services

The caller's transports (`adapters/`), the Open Host that answers them (`entrypoint/`), what a
call travels as and what a failed one raises (`domain/`). gRPC is the `[grpc]` extra and HTTP the
`[rpc]` one: `open_host` and `open_host_routes` load them only when asked.
"""

from typing import TYPE_CHECKING, Any

from sincpro_framework.common.transport.addresses import (
    HostedAt,
    HostedContext,
    InvalidAddress,
    Wire,
)
from sincpro_framework.remote_execution.domain.errors import (
    ContextFailed,
    ContextOutcomeUnknown,
    ContextTimeout,
    ContextUnavailable,
    DTODoesNotFit,
)
from sincpro_framework.remote_execution.entrypoint.hosts import (
    Attach,
    OpenHost,
    serve_contexts,
)

if TYPE_CHECKING:
    from sincpro_framework.remote_execution.entrypoint.grpc import open_host
    from sincpro_framework.remote_execution.entrypoint.http import open_host_routes

__all__ = [
    "Attach",
    "ContextFailed",
    "ContextOutcomeUnknown",
    "ContextTimeout",
    "ContextUnavailable",
    "DTODoesNotFit",
    "HostedAt",
    "HostedContext",
    "InvalidAddress",
    "OpenHost",
    "Wire",
    "open_host",
    "open_host_routes",
    "serve_contexts",
]


def __getattr__(name: str) -> Any:
    """Each door loads its wire only when asked: `open_host` needs the `[grpc]` extra,
    `open_host_routes` the `[rpc]` one."""
    if name == "open_host":
        from sincpro_framework.remote_execution.entrypoint import grpc

        return grpc.open_host
    if name == "open_host_routes":
        from sincpro_framework.remote_execution.entrypoint import http

        return http.open_host_routes
    raise AttributeError(
        f"module 'sincpro_framework.remote_execution' has no attribute {name!r}"
    )
