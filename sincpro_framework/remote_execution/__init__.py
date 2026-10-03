"""Remote execution: a bounded context executed by another service that runs the same code.

    # the caller — the conf file's `context_map`, or the environment
    SINCPRO_CONTEXT_MAP="billing=grpc://billing-service:50051?timeout=5"
    billing(CommandIssueInvoice(...), ResponseIssueInvoice)        # answered by that service

    # the host — its own deployment, beside a REST API, or in a subprocess
    billing.serve("0.0.0.0:50051", Attach.THREAD)

Context: in DDD the context map says where each bounded context lives and how the others reach
it; a context another service consumes is an Open Host Service. Where a context runs is
configuration, never code — the caller's `bus(dto, Response)` does not change. On the caller the
bus is a reference: a client with the face of the bus, never built, that forwards every call.

    domain/          the vocabulary and the port: address, payload, errors, transport
    adapters/        the transports, the caller's side: http (stdlib), grpc ([grpc] extra)
    configuration    the context map, read from the conf file and SINCPRO_CONTEXT_MAP
    entrypoint/      the Open Hosts: hosts (serve), http routes ([rpc]), grpc handler ([grpc])

See `docs/entrypoints/bounded-contexts-across-services.md`.
"""

from sincpro_framework.remote_execution.adapters import transport_for
from sincpro_framework.remote_execution.configuration import configured_host
from sincpro_framework.remote_execution.domain import (
    CHUNK_SIZE,
    CannotTravel,
    ChunkReader,
    ContextFailed,
    ContextOutcomeUnknown,
    ContextTimeout,
    ContextUnavailable,
    DTODoesNotFit,
    Transport,
    pack,
    pack_context,
    packed,
    unpack,
    unpack_context,
    unpacked,
)
from sincpro_framework.remote_execution.entrypoint import (
    Attach,
    OpenHost,
    open_host,
    serve_contexts,
)
from sincpro_framework.transport.addresses import (
    DEFAULT_TIMEOUT,
    HostedAt,
    HostedContext,
    InvalidAddress,
    Wire,
)

__all__ = [
    "open_host",
    "Attach",
    "CHUNK_SIZE",
    "CannotTravel",
    "ChunkReader",
    "ContextFailed",
    "ContextOutcomeUnknown",
    "ContextTimeout",
    "ContextUnavailable",
    "DEFAULT_TIMEOUT",
    "DTODoesNotFit",
    "HostedAt",
    "HostedContext",
    "InvalidAddress",
    "OpenHost",
    "Transport",
    "Wire",
    "configured_host",
    "pack",
    "pack_context",
    "packed",
    "serve_contexts",
    "transport_for",
    "unpack",
    "unpack_context",
    "unpacked",
]
