"""Remote execution: a bounded context executed by another service that runs the same code.

    # the caller — the conf file's `context_map`, or the environment
    SINCPRO_CONTEXT_MAP="billing=grpc://billing-service:50051?timeout=5"
    billing(CommandIssueInvoice(...), ResponseIssueInvoice)        # answered by that service

    # the host — its own deployment, beside a REST API, or in a subprocess
    billing.serve("0.0.0.0:50051", Attach.THREAD)

Context: in DDD the context map says where each bounded context lives and how the others reach
it; a context another service consumes is an Open Host Service. Where a context runs is
configuration, never code — the caller's `bus(dto, Response)` does not change.

    domain/          the vocabulary and the port: address, payload, errors, transport, hosting
    adapters/        the transports, the caller's side: http (stdlib), grpc ([grpc] extra)
    configuration    the context map, read from the conf file and SINCPRO_CONTEXT_MAP
    entrypoint/      the Open Hosts: hosts (serve), http routes ([rpc]), grpc handler ([grpc])

See `docs/entrypoints/bounded-contexts-across-services.md`.
"""

from sincpro_framework.remote_execution.adapters import transport_for
from sincpro_framework.remote_execution.configuration import configured_host
from sincpro_framework.remote_execution.domain import (
    CHUNK_SIZE,
    DEFAULT_TIMEOUT,
    CannotTravel,
    ChunkReader,
    ContextFailed,
    ContextTimeout,
    ContextUnavailable,
    HostedAt,
    Transport,
    context_map_of,
    hosted_here,
    hosting,
    pack,
    packed,
    parse_address,
    parse_context_map,
    unpack,
    unpacked,
)
from sincpro_framework.remote_execution.entrypoint import Attach, OpenHost, serve_contexts

__all__ = [
    "Attach",
    "CHUNK_SIZE",
    "CannotTravel",
    "ChunkReader",
    "ContextFailed",
    "ContextTimeout",
    "ContextUnavailable",
    "DEFAULT_TIMEOUT",
    "HostedAt",
    "OpenHost",
    "Transport",
    "configured_host",
    "context_map_of",
    "hosted_here",
    "hosting",
    "pack",
    "packed",
    "parse_address",
    "parse_context_map",
    "serve_contexts",
    "transport_for",
    "unpack",
    "unpacked",
]
