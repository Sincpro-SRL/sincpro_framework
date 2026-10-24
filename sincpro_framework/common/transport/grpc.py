"""gRPC's mechanics, shared by the public gRPC entrypoint and remote execution's internal host.

    server = transport.grpc.server(max_workers=16)              # threaded, 32 calls admitted
    server.add_generic_rpc_handlers((health_handler(["billing.v1.BillingService"], ready),))
    serve_until_terminated(server, "billing", grace=5.0)        # drains on SIGTERM/SIGINT

Context: what a server needs whatever it serves — the thread pool, the standard health service,
the request context, trace and deadline riding with the call, the caller's credentials, a bound on
the calls admitted at once, a graceful stop.
Nothing here decides what is published: the entrypoint mounts its declared surface, remote
execution its open host, each on a server built the same way.
"""

import signal
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent import futures
from typing import Any

from sincpro_framework.auth.domain.identity import Credentials
from sincpro_framework.auth.entrypoint.transports import credentials_from_headers
from sincpro_framework.context.adapters.propagation import extract
from sincpro_framework.context.domain.execution import IDENTITY_HEADERS
from sincpro_framework.observability.tracing.propagation import TRACE_HEADERS
from sincpro_framework.sincpro_logger import logger

GRPC_MISSING = "grpcio is not installed. Install with: pip install sincpro-framework[grpc]"
HEALTH_MISSING = (
    "grpcio-health-checking is not installed; serving without grpc.health.v1.Health. "
    "Install with: pip install sincpro-framework[grpc]"
)
HEALTH_SERVICE = "grpc.health.v1.Health"

try:
    import grpc  # pyright: ignore[reportMissingImports]
except ImportError as error:  # pragma: no cover - depends on the installed extra
    raise ImportError(GRPC_MISSING) from error

CONTEXT_PREFIX = "sp-ctx-"
CORRELATION_HEADER = "x-correlation-id"
HEADER_OF = {key: header for header, key in IDENTITY_HEADERS.items()}
SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGINT)
DEADLINE_KEY = "deadline"
"""The request context's key for the caller's deadline: epoch seconds, absent without one."""
NO_DEADLINE = 1e9
"""Seconds past which `time_remaining()` means "no deadline" — grpcio answers ~9.2e18 then."""


def server(
    max_workers: int = 10,
    interceptors: Iterable[Any] | None = None,
    options: Sequence[tuple[str, Any]] | None = None,
    maximum_concurrent_rpcs: int | None = None,
) -> Any:
    """A threaded `grpc.Server`, not started and with no port — the caller mounts what it serves.

    `maximum_concurrent_rpcs` (2 × `max_workers` by default) bounds the calls admitted at once:
    past it the server answers `RESOURCE_EXHAUSTED` at once, a status a client backs off on.

    Context: a thread pool, not `grpc.aio`: the bus is blocking, and the request context lives in
    a `ContextVar` the handler enters in its own worker thread. Without the bound, a burst queues
    silently behind the pool until every caller's own deadline fires."""
    return grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        interceptors=list(interceptors or ()),
        options=list(options or ()),
        maximum_concurrent_rpcs=(
            2 * max_workers if maximum_concurrent_rpcs is None else maximum_concurrent_rpcs
        ),
    )


def deadline_of(context: Any) -> float | None:
    """When the caller stops waiting, as epoch seconds — `None` when it set no deadline.

    Context: absolute, not the seconds remaining, so it stays true however long the request
    context travels before an adapter reads it for a timeout of its own."""
    remaining = context.time_remaining()
    if remaining is None or remaining >= NO_DEADLINE:
        return None
    return time.time() + remaining


def is_past(deadline: float | None) -> bool:
    """Whether the caller has already stopped waiting — never, when it set no deadline."""
    return deadline is not None and deadline <= time.time()


def context_from_metadata(metadata: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    """Fold call metadata into the framework context without touching the payload.

        context_from_metadata([("x-correlation-id", "C"), ("traceparent", "00-4bf9…-01"),
                               ("sp-ctx-tenant", "bo"), ("trace-bin", b"…")])
            ->  {"correlation_id": "C", "tenant": "bo",
                 "carrier": {"traceparent": "00-4bf9…-01"}}

    Context: the text entries go first through `extract` — `baggage` and `sincpro-context`
    are read there; a binary entry (`-bin`, or a value that is not text) is never read.

    1. `x-correlation-id`, `x-causation-id`, `x-execution-id` → the identity.
    2. `traceparent` / `tracestate` → carrier, for OTel parent adoption.
    3. `sp-ctx-<key>` → context[<key>] (tenant, user id, whatever the bus reads).
    4. Final: a context dict; empty when the caller sent none of them.
    """
    merged: dict[str, Any] = extract(
        {key: value for key, value in metadata if isinstance(value, str)}
    )
    carrier: dict[str, str] = {}
    for key, value in metadata:
        if key.endswith("-bin") or not isinstance(value, str):
            continue
        if key in IDENTITY_HEADERS:
            merged[IDENTITY_HEADERS[key]] = value
        elif key in TRACE_HEADERS:
            carrier[key] = value
        elif key.startswith(CONTEXT_PREFIX) and len(key) > len(CONTEXT_PREFIX):
            merged[key[len(CONTEXT_PREFIX) :]] = value
    if carrier:
        merged["carrier"] = carrier
    return merged


def metadata_from_context(
    context: Mapping[str, Any] | None,
) -> tuple[tuple[str, str], ...]:
    """The inverse of `context_from_metadata`, for a client that has a context dict.

        metadata_from_context({"correlation_id": "C", "tenant": "bo", "user": None,
                               "carrier": {"traceparent": "00-4bf9…-01", "x": "y"}})
            ->  (("x-correlation-id", "C"), ("sp-ctx-tenant", "bo"),
                 ("traceparent", "00-4bf9…-01"))

    Context: a `None` value is left out, every other value is written as text, and only the
    trace headers of the `carrier` travel.
    """
    metadata: list[tuple[str, str]] = []
    for key, value in (context or {}).items():
        if value is None:
            continue
        if key in HEADER_OF:
            metadata.append((HEADER_OF[key], str(value)))
        elif key == "carrier" and isinstance(value, Mapping):
            metadata.extend(
                (name, str(item)) for name, item in value.items() if name in TRACE_HEADERS
            )
        else:
            metadata.append((f"{CONTEXT_PREFIX}{key}", str(value)))
    return tuple(metadata)


def credentials_of(context: Any) -> Credentials:
    """The caller's credentials: its metadata as headers, and the client certificate of an
    mTLS channel — the first of the chain the server verified."""
    chain = context.auth_context().get("x509_pem_cert") or ()
    return credentials_from_headers(
        "grpc", context.invocation_metadata(), chain[0] if chain else None
    )


def health_handler(services: Iterable[str], is_ready: Callable[[], bool]) -> Any | None:
    """`grpc.health.v1.Health` for `services` — the one `grpcurl`, Envoy and Kubernetes' native
    gRPC probe speak. `Check` re-evaluates `is_ready` on every call; a service not listed answers
    `NOT_FOUND`; `""` is the whole server. `None`, with a warning, without
    `grpcio-health-checking`.

    Context: `grpc.health.v1` is a fixed, well-known protobuf `grpcio-health-checking` generates
    and ships — using its `health_pb2` is not the "no generated stubs" rule, which is about
    services derived from DTOs that have no field-number registry of their own.

        handler = health_handler(["billing.v1.BillingService"], is_ready=lambda: True)
        Check(service="billing.v1.BillingService")   ->  SERVING
        Check(service="")                            ->  SERVING     # the whole server
        Check(service="sales.v1.SalesService")       ->  NOT_FOUND
        # is_ready() now False: every known service  ->  NOT_SERVING

    1. `grpc_health` not installed: a warning and `None` — the server runs without it.
    2. The known names: the services given, the health service itself, and `""`.
    3. `Check`:
       3.1 a name not known is `NOT_FOUND`, with an empty response;
       3.2 else `SERVING` while `is_ready()`, `NOT_SERVING` when not — asked on each call.
    4. `Watch`: the answer of `Check`, once, then the stream ends.
    Final: a generic handler of `grpc.health.v1.Health` serving both methods.
    """
    try:
        from grpc_health.v1 import health_pb2  # pyright: ignore[reportMissingImports]
    except ImportError:
        logger.warning(HEALTH_MISSING)
        return None

    known = {*services, HEALTH_SERVICE, ""}

    def check(request: Any, context: Any) -> Any:
        """`Check`: a service this server does not host is `NOT_FOUND`; the empty name (the
        whole server) and every hosted one are `SERVING` while `is_ready()`, else
        `NOT_SERVING`."""
        if request.service and request.service not in known:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            return health_pb2.HealthCheckResponse()
        return health_pb2.HealthCheckResponse(
            status=(
                health_pb2.HealthCheckResponse.SERVING
                if is_ready()
                else health_pb2.HealthCheckResponse.NOT_SERVING
            )
        )

    def watch(request: Any, context: Any) -> Any:
        """`Watch`: the current status, once — the stream then ends rather than pushing
        changes."""
        yield check(request, context)

    return grpc.method_handlers_generic_handler(
        HEALTH_SERVICE,
        {
            "Check": grpc.unary_unary_rpc_method_handler(
                check,
                request_deserializer=health_pb2.HealthCheckRequest.FromString,
                response_serializer=health_pb2.HealthCheckResponse.SerializeToString,
            ),
            "Watch": grpc.unary_stream_rpc_method_handler(
                watch,
                request_deserializer=health_pb2.HealthCheckRequest.FromString,
                response_serializer=health_pb2.HealthCheckResponse.SerializeToString,
            ),
        },
    )


def hook_graceful_shutdown(server: Any, name: str, grace: float | None) -> None:
    """Drain in-flight calls on SIGTERM/SIGINT instead of the process dying mid-request.

    Context: `wait_for_termination()` blocks until something calls `stop()`, and nothing does by
    default — a rolling restart's SIGTERM would kill the calls in progress. `signal.signal` only
    works from the main thread: off it, this logs once and the server runs with no hook.

        hook_graceful_shutdown(server, "billing", grace=5.0)
        # kill -TERM <pid>  ->  "gRPC server [billing] received [SIGTERM], draining for up
        #                         to [5.0s]", then server.stop(5.0) — calls in flight finish

    Both SIGTERM and SIGINT are hooked; `grace=None` stops at once, aborting what runs.
    """

    def _shutdown(signum: int, _frame: Any) -> None:
        """The signal handler: logs the signal and stops the server with `grace` seconds for
        the calls in flight — what unblocks `wait_for_termination()`."""
        logger.info(
            "gRPC server [%s] received [%s], draining for up to [%ss]",
            name,
            signal.Signals(signum).name,
            grace,
        )
        server.stop(grace)

    try:
        for sig in SHUTDOWN_SIGNALS:
            signal.signal(sig, _shutdown)
    except ValueError:
        logger.warning(
            "gRPC server [%s] started off the main thread: SIGTERM/SIGINT cannot be hooked "
            "here, serving without a graceful-shutdown handler",
            name,
        )


def serve_until_terminated(
    server: Any, name: str, grace: float | None = 5.0, handle_signals: bool = True
) -> None:
    """Start `server` (its port already added) and block until it stops."""
    server.start()
    if handle_signals:
        hook_graceful_shutdown(server, name, grace)
    server.wait_for_termination()
