"""gRPC wire: Struct payloads, the one call path (generated methods and `bus_call`), rich status,
deadlines, health that drains, reflection v1 + v1alpha, the server's options.

Imports `grpc`, `protobuf` and `grpcio-status` at module level — importing this module is already
"I want to serve gRPC". `entrypoint.py` imports it lazily so `GrpcGateway` and the
`.proto` export stay usable without the `[grpc]` extra.

There is no generated `*_pb2_grpc.py` anywhere: services are registered as generic
handlers and described to reflection through a descriptor pool built at startup
from the same catalog. See `proto.py` for why the payload is `Struct`.
"""

import json
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, NoReturn

from pydantic import ValidationError

from sincpro_framework.auth.domain.exceptions import AuthError, Unauthenticated
from sincpro_framework.auth.entrypoint.transports import challenges_of, refusal_body
from sincpro_framework.common.failures import (
    FailureKind,
    failure_reason,
    json_safe_validation_errors,
    refined_failure_kind,
    retry_after,
    said_to_the_caller,
)
from sincpro_framework.common.transport.grpc import (
    DEADLINE_KEY,
    GRPC_MISSING,
    context_from_metadata,
    credentials_of,
    deadline_of,
    grpc,
    health_handler,
    is_past,
)
from sincpro_framework.context.domain.level import EntrypointKind
from sincpro_framework.ddd.exceptions import DuplicateAggregate
from sincpro_framework.entrypoints.adapters.grpc import naming, proto
from sincpro_framework.entrypoints.adapters.grpc.proto import GrpcMethodSpec
from sincpro_framework.entrypoints.domain.bindings import ExposureRefused
from sincpro_framework.entrypoints.domain.layers import RunFn, Scalar
from sincpro_framework.entrypoints.services.scalar_executor import dump_scalar_result, execute
from sincpro_framework.observability import process
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework

REFLECTION_MISSING = (
    "grpcio-reflection is not installed; serving without server reflection. "
    "Install with: pip install sincpro-framework[grpc]"
)

try:
    from google.protobuf import any_pb2, descriptor_pb2, descriptor_pool, json_format
    from google.protobuf.struct_pb2 import Struct
    from google.rpc import (  # pyright: ignore[reportMissingImports]
        error_details_pb2,
        status_pb2,
    )
    from grpc_status import rpc_status  # pyright: ignore[reportMissingImports]
except ImportError as error:  # pragma: no cover - depends on the installed extra
    raise ImportError(GRPC_MISSING) from error

MAX_CONNECTION_AGE_GRACE_MS = 30_000
KEEPALIVE_OPTIONS: tuple[tuple[str, int], ...] = (
    ("grpc.keepalive_time_ms", 60_000),
    ("grpc.keepalive_timeout_ms", 20_000),
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.min_recv_ping_interval_without_data_ms", 10_000),
)
"""A ping on an idle connection each minute, dead after 20 s without an answer — what a load
balancer that drops idle connections silently needs; clients may ping every 10 s."""

REFLECTION_METHOD = "ServerReflectionInfo"
REFLECTION_SERVICES = (
    "grpc.reflection.v1.ServerReflection",
    "grpc.reflection.v1alpha.ServerReflection",
)

CODE_OF = {
    FailureKind.INVALID: grpc.StatusCode.INVALID_ARGUMENT,
    FailureKind.UNAUTHENTICATED: grpc.StatusCode.UNAUTHENTICATED,
    FailureKind.PERMISSION_DENIED: grpc.StatusCode.PERMISSION_DENIED,
    FailureKind.NOT_FOUND: grpc.StatusCode.NOT_FOUND,
    FailureKind.CONFLICT: grpc.StatusCode.ABORTED,
    FailureKind.IN_PROGRESS: grpc.StatusCode.ABORTED,
    FailureKind.KEY_REUSED: grpc.StatusCode.FAILED_PRECONDITION,
    FailureKind.DOMAIN: grpc.StatusCode.FAILED_PRECONDITION,
    FailureKind.EXHAUSTED: grpc.StatusCode.RESOURCE_EXHAUSTED,
    FailureKind.UNAVAILABLE: grpc.StatusCode.UNAVAILABLE,
    FailureKind.UNKNOWN_OUTCOME: grpc.StatusCode.DEADLINE_EXCEEDED,
    FailureKind.INTERNAL: grpc.StatusCode.INTERNAL,
}
"""Each kind's code (PRD_15 §1.3). A duplicate is `ALREADY_EXISTS`, the one conflict it refines."""

BROKE_A_RULE = frozenset({FailureKind.DOMAIN, FailureKind.KEY_REUSED})
"""The kinds whose answer names the rule broken — `PreconditionFailure`, per AIP-193."""


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


class RichStatus(grpc.Status):
    """A `grpc.Status` for `abort_with_status`: a code, its message, and the trailing metadata
    that carries the `google.rpc.Status` — plus what else the call answers with (an auth
    refusal's challenge), since `abort_with_status` replaces any trailing metadata set before.
    """

    def __init__(self, code: Any, details: str, trailing_metadata: tuple[Any, ...]):
        self.code = code
        self.details = details
        self.trailing_metadata = trailing_metadata


def _packed(message: Any) -> Any:
    found = any_pb2.Any()
    found.Pack(message)
    return found


def _field_violations(error: ValidationError) -> Any:
    request = error_details_pb2.BadRequest()
    for one in json_safe_validation_errors(error):
        violation = request.field_violations.add()
        violation.field = ".".join(str(part) for part in one.get("loc", ()))
        violation.description = str(one.get("msg", ""))
        if "reason" in violation.DESCRIPTOR.fields_by_name:
            violation.reason = str(one.get("type", "")).upper()
    return request


def _told(error: Exception, kind: FailureKind, code: Any) -> str:
    """The status message: the validation errors as JSON, a domain error's own words, or the
    code's name — never the inside of the process."""
    if isinstance(error, ValidationError):
        return json.dumps(json_safe_validation_errors(error))
    disclosed = said_to_the_caller(error)
    if disclosed is not None:
        return disclosed
    return (
        "Internal error"
        if kind == FailureKind.INTERNAL
        else code.name.replace("_", " ").capitalize()
    )


def status_for(
    error: Exception, domain: str, subject: str, framework: UseFramework
) -> RichStatus:
    """The `google.rpc.Status` a failure the bus raised is answered with (AIP-193).

    1. The kind (`common.failures.refined_failure_kind`) picks the code — a duplicate is
       ALREADY_EXISTS, the conflict it refines.
    2. `ErrorInfo` always: `reason` the stable UPPER_SNAKE a client switches on, `domain` the
       served package, `metadata.kind` the kind, and an auth refusal's fields.
    3. Per kind: `BadRequest` per invalid field, `PreconditionFailure` for a rule the domain
       refused (its subject the Command), `RetryInfo` when retrying as it is will work.
    4. Final: the status, carried in `grpc-status-details-bin`; an auth refusal also answers
       `sp-auth-refusal` and, for nobody known, `www-authenticate`.
    """
    kind = refined_failure_kind(error)
    code = (
        grpc.StatusCode.ALREADY_EXISTS
        if kind == FailureKind.CONFLICT and isinstance(error, DuplicateAggregate)
        else CODE_OF[kind]
    )
    message = _told(error, kind, code)
    reason = failure_reason(error, kind)
    info = error_details_pb2.ErrorInfo(reason=reason, domain=domain)
    info.metadata["kind"] = kind.value
    if isinstance(error, AuthError):
        for key, value in refusal_body(error).items():
            info.metadata[key] = str(value)
    details: list[Any] = [info]
    if isinstance(error, ValidationError):
        details.append(_field_violations(error))
    if kind in BROKE_A_RULE:
        failure = error_details_pb2.PreconditionFailure()
        failure.violations.add(type=reason, subject=subject, description=message)
        details.append(failure)
    delay = retry_after(error, kind)
    if delay is not None:
        retry = error_details_pb2.RetryInfo()
        retry.retry_delay.FromTimedelta(delay)
        details.append(retry)
    status = status_pb2.Status(
        code=code.value[0], message=message, details=[_packed(one) for one in details]
    )
    trailing = tuple(rpc_status.to_status(status).trailing_metadata)
    if isinstance(error, AuthError):
        trailing += refusal_metadata(error, framework)
    return RichStatus(code, message, trailing)


def refusal_metadata(
    error: AuthError, framework: UseFramework
) -> tuple[tuple[str, str], ...]:
    """What a refused caller reads besides the code: why, and — for nobody known — where to get
    a credential, as `www-authenticate`."""
    metadata = [("sp-auth-refusal", json.dumps(refusal_body(error)))]
    if isinstance(error, Unauthenticated):
        metadata += [("www-authenticate", one) for one in challenges_of([framework])]
    return tuple(metadata)


def deadline_exceeded(domain: str) -> RichStatus:
    code = grpc.StatusCode.DEADLINE_EXCEEDED
    info = error_details_pb2.ErrorInfo(reason=code.name, domain=domain)
    status = status_pb2.Status(
        code=code.value[0],
        message="The caller's deadline passed before the call ran",
        details=[_packed(info)],
    )
    return RichStatus(
        code, status.message, tuple(rpc_status.to_status(status).trailing_metadata)
    )


def _abort(context: Any, status: RichStatus) -> NoReturn:
    context.abort_with_status(status)
    raise RuntimeError("abort_with_status returned instead of raising")


def through_the_bus(
    context: Any,
    framework: UseFramework,
    domain: str,
    subject: Callable[[], str],
    run: RunFn,
    payload: Scalar,
) -> Scalar:
    """The one path every gRPC call takes to the bus — a generated method and `bus_call` alike.

    1. Metadata → framework context, the caller's credentials, the caller's deadline →
       `context["deadline"]` (epoch seconds) for adapters' own timeouts.
    2. A call whose deadline already passed answers DEADLINE_EXCEEDED and never runs: its caller
       gave up, and a write it no longer waits for is a write nobody sees.
    3. `run` on the bus, authenticated in this same thread (identity is a ContextVar).
    4. A failure is answered as a `google.rpc.Status` (`status_for`); a validation error is the
       caller's mistake and is not logged as a failure.
    5. Final: what `run` answered.
    """
    call_context = context_from_metadata(context.invocation_metadata())
    credentials = credentials_of(context)
    deadline = deadline_of(context)
    if deadline is not None:
        call_context[DEADLINE_KEY] = deadline
    if is_past(deadline):
        _abort(context, deadline_exceeded(domain))
    try:
        return execute(
            framework, run, payload, call_context or None, credentials, EntrypointKind.GRPC
        )
    except Exception as error:
        if not isinstance(error, ValidationError) and not process.was_reported(error):
            logger.exception("gRPC call [%s] in [%s] failed", subject(), domain)
        _abort(context, status_for(error, domain, subject(), framework))


def method_handler(spec: GrpcMethodSpec) -> Any:
    """Bind one spec to a unary-unary handler over Struct: the payload is validated as the DTO
    inside `run`, on the one path (`through_the_bus`)."""
    command = spec.operation.command.__name__

    def handle(request: Struct, context: grpc.ServicerContext) -> Struct:
        answered = through_the_bus(
            context,
            spec.framework,
            spec.package,
            lambda: command,
            spec.operation.run,
            struct_to_scalar(request),
        )
        return scalar_to_struct(answered)

    return handle


def bus_call[R](
    context: Any,
    bus: UseFramework,
    dto: DataTransferObject | Callable[[], DataTransferObject],
    to_response: Callable[[Any], R] | None = None,
    domain: str | None = None,
) -> R | Struct:
    """A hand-written servicer's call to the bus, on the path a generated method takes.

    `dto` is the Command, or a function building it — built inside, its validation error is
    answered INVALID_ARGUMENT with `BadRequest` as a generated method's is. `to_response` turns
    what the bus answered into the servicer's message; without it the answer is a `Struct`.
    `domain` names the package in `ErrorInfo` — the bus's default package (`billing.v1`) when
    not given.
    """
    answered: list[Any] = []
    built: list[str] = []

    def run(_payload: Scalar) -> Scalar:
        command = dto() if callable(dto) else dto
        built.append(type(command).__name__)
        answered.append(bus(command))
        return {}

    through_the_bus(
        context,
        bus,
        domain or naming.default_package(bus.name),
        lambda: built[0] if built else "",
        run,
        {},
    )
    if to_response is None:
        return scalar_to_struct(dump_scalar_result(answered[0]))
    return to_response(answered[0])


def _package_of(method: str) -> str:
    """`/billing.v1.BillingService/IssueInvoice` → `billing.v1`."""
    service = method.lstrip("/").split("/", 1)[0]
    return service.rsplit(".", 1)[0] if "." in service else service


class RefusePastDeadline(grpc.ServerInterceptor):
    """A call whose deadline passed before its handler is reached answers DEADLINE_EXCEEDED
    without running — for a hand-written servicer that never reaches `bus_call` too."""

    def intercept_service(self, continuation: Any, handler_call_details: Any) -> Any:
        handler = continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler
        inner = handler.unary_unary
        domain = _package_of(handler_call_details.method)

        def guarded(request: Any, context: Any) -> Any:
            if is_past(deadline_of(context)):
                _abort(context, deadline_exceeded(domain))
            return inner(request, context)

        return grpc.unary_unary_rpc_method_handler(
            guarded,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


class LogCalls(grpc.ServerInterceptor):
    """One debug line per unary call: its method, whether it answered, how long it took."""

    def intercept_service(self, continuation: Any, handler_call_details: Any) -> Any:
        handler = continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler
        inner = handler.unary_unary
        method = handler_call_details.method

        def logged(request: Any, context: Any) -> Any:
            started = time.perf_counter()
            outcome = "refused"
            try:
                answered = inner(request, context)
                outcome = "answered"
                return answered
            finally:
                logger.debug(
                    "gRPC [%s] %s in [%.1f ms]",
                    method,
                    outcome,
                    (time.perf_counter() - started) * 1000,
                )

        return grpc.unary_unary_rpc_method_handler(
            logged,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


def framework_interceptors() -> list[Any]:
    """What a project's own `grpc.server(...)` adds to run hand-written servicers as the
    gateway runs its own: the deadline pre-check and a call log. Never auth — the bus's
    `AccessControl` is the only guard, and `bus_call` reaches it."""
    return [RefusePastDeadline(), LogCalls()]


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


class Readiness:
    """What `grpc.health.v1.Health` answers for every service of a gateway: its check, until the
    gateway drains — from then on NOT_SERVING, whatever the check says.

    Context: flipped on SIGTERM before `stop(grace)`, so a probe or a load balancer watching
    health stops sending new calls while the ones in flight finish."""

    def __init__(self, check: Callable[[], bool], draining: threading.Event):
        self.check = check
        self.draining = draining

    def __call__(self) -> bool:
        return not self.draining.is_set() and self.check()


def default_health_check(specs: Mapping[str, GrpcMethodSpec]) -> Callable[[], bool]:
    """Every published `UseFramework` is ready — built, or a reference — verified live on every `Check` instead of
    assumed once. A caller wanting a deeper probe (a DB ping, a queue connection) passes its
    own `health_check`."""
    frameworks = {spec.framework for spec in specs.values()}

    def check() -> bool:
        return all(f.is_ready for f in frameworks)

    return check


def generic_handlers(
    specs: Mapping[str, GrpcMethodSpec], document: Scalar, is_ready: Callable[[], bool]
) -> list[Any]:
    """One generic handler per service, the Introspection service, and — when
    `grpcio-health-checking` is installed — the standard `grpc.health.v1.Health`."""
    handlers = [
        grpc.method_handlers_generic_handler(
            service, {spec.method: _unary(method_handler(spec)) for spec in entries}
        )
        for service, entries in proto.group_by_service(specs).items()
    ]
    handlers.append(
        grpc.method_handlers_generic_handler(
            proto.DESCRIBE_SERVICE,
            {proto.DESCRIBE_METHOD: _unary(describe_handler(document))},
        )
    )
    health = health_handler(
        [*proto.group_by_service(specs), proto.DESCRIBE_SERVICE], is_ready
    )
    if health is not None:
        handlers.append(health)
    return handlers


def _file_descriptor_proto(package: str, services: Mapping[str, list[GrpcMethodSpec]]) -> Any:
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = f"sincpro/{proto.file_name(package)}"
    file_proto.package = package
    file_proto.syntax = "proto3"
    file_proto.dependency.append(proto.STRUCT_IMPORT)
    for service, entries in services.items():
        service_proto = file_proto.service.add()
        service_proto.name = service
        for spec in entries:
            method_proto = service_proto.method.add()
            method_proto.name = spec.method
            method_proto.input_type = f".{proto.STRUCT_TYPE}"
            method_proto.output_type = f".{proto.STRUCT_TYPE}"
            if spec.deprecated:
                method_proto.options.deprecated = True
    return file_proto


def _introspection_file() -> Any:
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = f"sincpro/{proto.INTROSPECTION_PACKAGE}.proto"
    file_proto.package = proto.INTROSPECTION_PACKAGE
    file_proto.syntax = "proto3"
    file_proto.dependency.append(proto.STRUCT_IMPORT)
    method_proto = file_proto.service.add(name=proto.INTROSPECTION_SERVICE).method.add()
    method_proto.name = proto.DESCRIBE_METHOD
    method_proto.input_type = method_proto.output_type = f".{proto.STRUCT_TYPE}"
    return file_proto


def _reflection_file(package: str, source: Any) -> Any:
    """`source` — v1alpha's `reflection.proto` — renamed to `package`: v1 is the same file
    field for field, only its package and name differ."""
    renamed = descriptor_pb2.FileDescriptorProto.FromString(source.serialized_pb)
    before = f".{renamed.package}."
    renamed.name = f"{package.replace('.', '/')}/reflection.proto"
    renamed.package = package
    for message in renamed.message_type:
        for field in message.field:
            if field.HasField("type_name"):
                field.type_name = field.type_name.replace(before, f".{package}.")
    for service in renamed.service:
        for method in service.method:
            method.input_type = method.input_type.replace(before, f".{package}.")
            method.output_type = method.output_type.replace(before, f".{package}.")
    return renamed


def _add_file(pool: Any, file: Any, added: set[str]) -> None:
    """A compiled file and, first, every file it imports — each once."""
    if file.name in added:
        return
    for dependency in file.dependencies:
        _add_file(pool, dependency, added)
    copied = descriptor_pb2.FileDescriptorProto()
    file.CopyToProto(copied)
    pool.Add(copied)
    added.add(file.name)


def services_of(files: Iterable[Any]) -> list[str]:
    """The fully qualified services compiled `*_pb2.DESCRIPTOR` files declare."""
    return [service.full_name for file in files for service in file.services_by_name.values()]


def paths_of(files: Iterable[Any]) -> set[str]:
    """Every method path compiled `*_pb2.DESCRIPTOR` files declare."""
    return {
        f"/{service.full_name}/{method.name}"
        for file in files
        for service in file.services_by_name.values()
        for method in service.methods
    }


def refuse_clashes(specs: Mapping[str, GrpcMethodSpec], extra: Sequence[Any]) -> None:
    """A hand-written method on a path the gateway also serves fails the build: which of the two
    answers would depend on mounting order."""
    clashing = sorted(paths_of(extra) & set(specs))
    if clashing:
        raise ExposureRefused(
            "hand-written methods the gateway also serves: "
            + ", ".join(
                f"{path} ({specs[path].operation.command.__name__})" for path in clashing
            )
            + " — exclude the use case from the gateway, or rename the hand-written method"
        )


def descriptor_pool_for(
    specs: Mapping[str, GrpcMethodSpec],
    reflection_pb2: Any = None,
    extra: Sequence[Any] = (),
) -> Any:
    """A private pool describing the served services, the hand-written ones in `extra`, and —
    given `reflection_pb2` — both reflection services themselves.

    Private, not `descriptor_pool.Default()`: two gateways in one process (or one rebuilt
    between tests) would otherwise add the same file name twice.
    """
    pool = descriptor_pool.DescriptorPool()
    added: set[str] = set()
    _add_file(pool, Struct.DESCRIPTOR.file, added)
    for package, services in proto.group_by_package(specs).items():
        pool.Add(_file_descriptor_proto(package, services))
    pool.Add(_introspection_file())
    for file in extra:
        _add_file(pool, file, added)
    if reflection_pb2 is not None:
        for service in REFLECTION_SERVICES:
            package = service.rsplit(".", 1)[0]
            pool.Add(_reflection_file(package, reflection_pb2.DESCRIPTOR))
    return pool


def reflection_handlers(
    specs: Mapping[str, GrpcMethodSpec], extra: Sequence[Any] = ()
) -> tuple[Any, ...]:
    """Server reflection, v1 and v1alpha, over the served services and the hand-written ones in
    `extra` — `()`, with a warning, without `grpcio-reflection`.

    `grpcio-reflection` serves v1alpha only, and current clients ask v1 first: the two are the
    same messages field for field, so one servicer answers both.
    """
    try:
        from grpc_reflection.v1alpha import (  # pyright: ignore[reportMissingImports]
            reflection,
            reflection_pb2,
        )
    except ImportError:
        logger.warning(REFLECTION_MISSING)
        return ()

    names = [
        *proto.group_by_service(specs),
        *services_of(extra),
        proto.DESCRIBE_SERVICE,
        *REFLECTION_SERVICES,
    ]
    servicer = reflection.ReflectionServicer(
        names, pool=descriptor_pool_for(specs, reflection_pb2, extra)
    )
    method = grpc.stream_stream_rpc_method_handler(
        servicer.ServerReflectionInfo,
        request_deserializer=reflection_pb2.ServerReflectionRequest.FromString,
        response_serializer=reflection_pb2.ServerReflectionResponse.SerializeToString,
    )
    return tuple(
        grpc.method_handlers_generic_handler(service, {REFLECTION_METHOD: method})
        for service in REFLECTION_SERVICES
    )


def server_options(
    options: Sequence[tuple[str, Any]] | None = None,
    max_message_bytes: int = proto.MAX_MESSAGE_BYTES,
    max_connection_age: float | None = proto.MAX_CONNECTION_AGE,
) -> list[tuple[str, Any]]:
    """The server's channel options: keepalive, `max_connection_age`, the message size limit
    both ways — each replaced by the caller's option of the same key.

    `max_connection_age=None` keeps connections for ever (grpcio's default)."""
    chosen: dict[str, Any] = dict(KEEPALIVE_OPTIONS)
    chosen["grpc.max_receive_message_length"] = max_message_bytes
    chosen["grpc.max_send_message_length"] = max_message_bytes
    if max_connection_age is not None:
        chosen["grpc.max_connection_age_ms"] = int(max_connection_age * 1000)
        chosen["grpc.max_connection_age_grace_ms"] = MAX_CONNECTION_AGE_GRACE_MS
    chosen.update(dict(options or ()))
    return list(chosen.items())
