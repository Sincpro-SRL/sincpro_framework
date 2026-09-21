"""gRPC wire: Struct payloads, generic handlers, status mapping, reflection.

Imports `grpc` and `protobuf` at module level — importing this module is already
"I want to serve gRPC". `entrypoint.py` imports it lazily so `GrpcGateway` and the
`.proto` export stay usable without the `[grpc]` extra.

There is no generated `*_pb2_grpc.py` anywhere: services are registered as generic
handlers and described to reflection through a descriptor pool built at startup
from the same catalog. See `proto.py` for why the payload is `Struct`.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from concurrent import futures
from typing import Any

from pydantic import ValidationError

from sincpro_framework.entrypoints.const import Scalar
from sincpro_framework.entrypoints.errors import (
    json_safe_validation_errors,
    said_to_the_caller,
)
from sincpro_framework.entrypoints.grpc import proto
from sincpro_framework.entrypoints.grpc.proto import GrpcMethodSpec
from sincpro_framework.entrypoints.scalar_executor import execute
from sincpro_framework.sincpro_logger import logger

GRPC_MISSING = "grpcio is not installed. Install with: pip install sincpro-framework[grpc]"
REFLECTION_MISSING = (
    "grpcio-reflection is not installed; serving without server reflection. "
    "Install with: pip install sincpro-framework[grpc]"
)
HEALTH_MISSING = (
    "grpcio-health-checking is not installed; serving without grpc.health.v1.Health. "
    "Install with: pip install sincpro-framework[grpc]"
)
HEALTH_SERVICE = "grpc.health.v1.Health"

try:
    import grpc  # pyright: ignore[reportMissingImports]
    from google.protobuf import descriptor_pb2, descriptor_pool, json_format
    from google.protobuf.struct_pb2 import Struct
except ImportError as error:  # pragma: no cover - depends on the installed extra
    raise ImportError(GRPC_MISSING) from error

CONTEXT_PREFIX = "sp-ctx-"
CORRELATION_HEADER = "x-correlation-id"
TRACE_HEADERS = ("traceparent", "tracestate")


def struct_to_scalar(message: Struct) -> Scalar:
    return json_format.MessageToDict(message)


def scalar_to_struct(payload: Scalar) -> Struct:
    """Scalar → Struct, falling back to a JSON round-trip for exotic leaves.

    `dump_scalar_result` guarantees the payload survives `json.dumps`, which still
    admits values `ParseDict` refuses (a tuple, a dict keyed by int). Re-parsing
    the JSON normalises those instead of failing after the Feature already ran.
    """
    message = Struct()
    try:
        json_format.ParseDict(payload, message)
    except (ValueError, TypeError):
        json_format.ParseDict(json.loads(json.dumps(payload, default=str)), message)
    return message


def context_from_metadata(metadata: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    """Fold call metadata into the framework context without touching the payload.

    gRPC has no sibling of `params` the way JSON-RPC's `context` is, so the
    transport carries it: DTO fields stay the only thing inside the Struct.

    1. `x-correlation-id` → correlation_id.
    2. `traceparent` / `tracestate` → carrier, for OTel parent adoption.
    3. `sp-ctx-<key>` → context[<key>] (tenant, user id, whatever the bus reads).
    4. Final: a context dict; empty when the caller sent none of them.
    """
    merged: dict[str, Any] = {}
    carrier: dict[str, str] = {}
    for key, value in metadata:
        if key.endswith("-bin") or not isinstance(value, str):
            continue
        if key == CORRELATION_HEADER:
            merged["correlation_id"] = value
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
    """The inverse of `context_from_metadata`, for a client that has a context dict."""
    metadata: list[tuple[str, str]] = []
    for key, value in (context or {}).items():
        if value is None:
            continue
        if key == "correlation_id":
            metadata.append((CORRELATION_HEADER, str(value)))
        elif key == "carrier" and isinstance(value, Mapping):
            metadata.extend(
                (name, str(item)) for name, item in value.items() if name in TRACE_HEADERS
            )
        else:
            metadata.append((f"{CONTEXT_PREFIX}{key}", str(value)))
    return tuple(metadata)


def status_for(error: Exception) -> tuple[Any, str]:
    """Map a failure the bus raised to a gRPC status.

    A `DomainError` is the answer to the request — FAILED_PRECONDITION with its
    own message. Anything else is the inside of the process: INTERNAL, and the
    message stays in the log (see `entrypoints.errors.said_to_the_caller`).
    """
    disclosed = said_to_the_caller(error)
    if disclosed is not None:
        return grpc.StatusCode.FAILED_PRECONDITION, disclosed
    return grpc.StatusCode.INTERNAL, "Internal error"


def method_handler(spec: GrpcMethodSpec) -> Any:
    """Bind one spec to a unary-unary handler over Struct.

    1. Struct → Scalar, metadata → framework context.
    2. Execute on the bus (DTO validation and Value Objects run inside `run`).
    3. A ValidationError is the caller's mistake → INVALID_ARGUMENT, details are
       the Pydantic errors as JSON.
    4. Final: the response Scalar as a Struct.
    """

    def handle(request: Struct, context: grpc.ServicerContext) -> Struct:
        payload = struct_to_scalar(request)
        call_context = context_from_metadata(context.invocation_metadata())
        try:
            result = execute(
                spec.framework, spec.operation.run, payload, call_context or None
            )
        except ValidationError as error:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                json.dumps(json_safe_validation_errors(error)),
            )
        except Exception as error:
            logger.exception("gRPC method [%s] failed", spec.path)
            code, details = status_for(error)
            context.abort(code, details)
        return scalar_to_struct(result)

    return handle


def describe_handler(document: Scalar) -> Any:
    def handle(_request: Struct, _context: grpc.ServicerContext) -> Struct:
        return scalar_to_struct(document)

    return handle


def _unary(handler: Any) -> Any:
    return grpc.unary_unary_rpc_method_handler(
        handler,
        request_deserializer=Struct.FromString,
        response_serializer=Struct.SerializeToString,
    )


def generic_handlers(
    specs: Mapping[str, GrpcMethodSpec],
    document: Scalar,
    health_check: Any | None = None,
) -> list[Any]:
    """One generic handler per service, the Introspection service, and — when
    `grpcio-health-checking` is installed — the standard `grpc.health.v1.Health`.

    Health is on by default: a gateway that fails to build never reaches this call
    (`Catalog` already forces `build_root_bus()`), so serving without a way for k8s,
    a mesh, or `grpcurl` to ask "are you actually up" is the wrong default, not the
    safe one. Missing the extra logs a warning and serves the rest regardless —
    never a failed start over an optional probe.
    """
    handlers = [
        grpc.method_handlers_generic_handler(
            service,
            {spec.method: _unary(method_handler(spec)) for spec in entries},
        )
        for service, entries in proto.group_by_service(specs).items()
    ]
    handlers.append(
        grpc.method_handlers_generic_handler(
            proto.DESCRIBE_SERVICE,
            {proto.DESCRIBE_METHOD: _unary(describe_handler(document))},
        )
    )
    health = health_handler(specs, health_check)
    if health is not None:
        handlers.append(health)
    return handlers


def default_health_check(specs: Mapping[str, GrpcMethodSpec]) -> Any:
    """Every registered `UseFramework` is built — the same thing `Catalog` already
    forces at construction, verified live on every `Check` instead of assumed once.

    Good enough as a default because a gateway that failed to build never reaches
    this call in the first place; a caller wanting a deeper probe (a DB ping, a
    queue connection) passes its own `health_check` to `.server()`/`.run()`.
    """
    frameworks = {spec.framework for spec in specs.values()}

    def check() -> bool:
        return all(f.was_initialized and f.bus is not None for f in frameworks)

    return check


def health_handler(
    specs: Mapping[str, GrpcMethodSpec], health_check: Any | None = None
) -> Any | None:
    """`grpc.health.v1.Health`, the one `grpcurl`, Envoy and Kubernetes' native gRPC
    probe already speak — `Check` re-evaluates `health_check` (or `default_health_check`)
    on every call, `Watch` streams the same status once per connection.

    Unlike this package's own services, `grpc.health.v1` is a fixed, well-known
    protobuf a third-party package (`grpcio-health-checking`) already generates and
    ships — using its own `health_pb2`/`HealthCheckResponse` here is not the "no
    generated `*_pb2_grpc.py`" rule from `proto.py` being broken; that rule is about
    services this module invents per bus, which have no field-number registry of
    their own. Health has neither problem: the protobuf is fixed and comes from
    upstream, not derived from a DTO.
    """
    try:
        from grpc_health.v1 import health_pb2  # pyright: ignore[reportMissingImports]
    except ImportError:
        logger.warning(HEALTH_MISSING)
        return None

    is_ready = health_check or default_health_check(specs)
    known = {*proto.group_by_service(specs), proto.DESCRIBE_SERVICE, HEALTH_SERVICE, ""}

    def live_status() -> Any:
        return (
            health_pb2.HealthCheckResponse.SERVING
            if is_ready()
            else health_pb2.HealthCheckResponse.NOT_SERVING
        )

    def check(request: Any, context: grpc.ServicerContext) -> Any:
        if request.service and request.service not in known:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            return health_pb2.HealthCheckResponse()
        return health_pb2.HealthCheckResponse(status=live_status())

    def watch(request: Any, context: grpc.ServicerContext) -> Any:
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


def _file_descriptor_proto(package: str, services: Mapping[str, list[str]]) -> Any:
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = f"sincpro/{package}.proto"
    file_proto.package = package
    file_proto.syntax = "proto3"
    file_proto.dependency.append(proto.STRUCT_IMPORT)
    for service, methods in services.items():
        service_proto = file_proto.service.add()
        service_proto.name = service
        for method in methods:
            method_proto = service_proto.method.add()
            method_proto.name = method
            method_proto.input_type = f".{proto.STRUCT_TYPE}"
            method_proto.output_type = f".{proto.STRUCT_TYPE}"
    return file_proto


def descriptor_pool_for(specs: Mapping[str, GrpcMethodSpec]) -> Any:
    """A private pool describing the served services, for server reflection.

    Private, not `descriptor_pool.Default()`: two gateways in one process (or one
    rebuilt between tests) would otherwise add the same file name twice and the
    second Add would raise over the first one's content.
    """
    pool = descriptor_pool.DescriptorPool()
    pool.Add(
        descriptor_pb2.FileDescriptorProto.FromString(Struct.DESCRIPTOR.file.serialized_pb)
    )
    packages: dict[str, dict[str, list[str]]] = {}
    for service, entries in proto.group_by_service(specs).items():
        package, name = service.split(".", 1)
        packages.setdefault(package, {})[name] = [spec.method for spec in entries]
    packages.setdefault(proto.INTROSPECTION_PACKAGE, {})[proto.INTROSPECTION_SERVICE] = [
        proto.DESCRIBE_METHOD
    ]
    for package, services in packages.items():
        pool.Add(_file_descriptor_proto(package, services))
    return pool


def enable_reflection(server: Any, specs: Mapping[str, GrpcMethodSpec]) -> bool:
    """Publish the served services over gRPC server reflection when available.

    grpcurl and Postman list and call methods from this; without it a client needs
    the exported `.proto`. Absence is a warning, never a failed start.
    """
    try:
        from grpc_reflection.v1alpha import (  # pyright: ignore[reportMissingImports]
            reflection,
        )
    except ImportError:
        logger.warning(REFLECTION_MISSING)
        return False

    names = [
        *proto.group_by_service(specs),
        proto.DESCRIBE_SERVICE,
        reflection.SERVICE_NAME,
    ]
    reflection.enable_server_reflection(names, server, descriptor_pool_for(specs))
    return True


def build_server(
    specs: Mapping[str, GrpcMethodSpec],
    document: Scalar,
    max_workers: int = 10,
    interceptors: Iterable[Any] | None = None,
    options: Sequence[tuple[str, Any]] | None = None,
    reflection: bool = True,
    health_check: Any | None = None,
) -> Any:
    """A `grpc.Server` serving the catalog. Not started; the caller adds the port.

    A thread pool, not `grpc.aio`: `execute` is blocking and `framework.context`
    lives in a ContextVar the handler enters in its own worker thread.
    """
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        interceptors=list(interceptors or ()),
        options=list(options or ()),
    )
    server.add_generic_rpc_handlers(tuple(generic_handlers(specs, document, health_check)))
    if reflection:
        enable_reflection(server, specs)
    return server
