"""gRPC gateway: wrap one or more UseFramework instances as gRPC services.

`.server()`/`.run()` are the batteries-included path: this gateway's own `grpc.Server`,
started and bound to a port. `.handlers()` is the composable primitive underneath them — the
generic RPC handlers with no server around them — for a process that builds its own
`grpc.Server` (its own executor, its own lifecycle, `grpc.aio` instead of the threaded one),
mounts several gateways' services on one port, or wires a `grpc.ServerInterceptor` of its own
for auth/logging. Per-bus granularity there is the interceptor's own job: `HandlerCallDetails
.method` is already `/{alias}.{Service}/{Method}`, so one interceptor branching on that prefix
reaches every bus differently without this module knowing what "auth" means.
"""

import signal
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Self

from sincpro_framework.entrypoints.catalog import Catalog
from sincpro_framework.entrypoints.const import Layer, Scalar, Wrapper
from sincpro_framework.entrypoints.grpc import proto
from sincpro_framework.entrypoints.grpc.proto import GrpcMethodSpec
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework

DEFAULT_LAYERS = (Layer.APP_SERVICES, Layer.FEATURES)
DEFAULT_ADDRESS = "127.0.0.1:50051"
SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def _hook_graceful_shutdown(server: Any, title: str, grace: float | None) -> None:
    """Drain in-flight calls on SIGTERM/SIGINT instead of the process dying mid-request.

    `grpc.Server.wait_for_termination()` blocks until something calls `.stop()`;
    nothing does by default, so a rolling restart's SIGTERM falls through to Python's
    own default action (the process dies, in-flight calls included) instead of a
    graceful drain. `signal.signal` only works from the main thread — a `run()` off
    the main thread (a background worker, a notebook cell) logs once and serves with
    no hook, same as `handle_signals=False`, rather than raising.
    """

    def _shutdown(signum: int, _frame: Any) -> None:
        logger.info(
            "gRPC gateway [%s] received [%s], draining for up to [%ss]",
            title,
            signal.Signals(signum).name,
            grace,
        )
        server.stop(grace)

    try:
        for sig in SHUTDOWN_SIGNALS:
            signal.signal(sig, _shutdown)
    except ValueError:
        logger.warning(
            "gRPC gateway [%s] started off the main thread: SIGTERM/SIGINT cannot be "
            "hooked here, serving without a graceful-shutdown handler",
            title,
        )


class GrpcGateway:
    """gRPC facade over one or more UseFramework instances.

    Same catalog as `entrypoint_rpc`, different wire: `qr.features.ChargePayment`
    over JSON-RPC is `/qr.Features/ChargePayment` here. Every method is unary and
    takes/returns `google.protobuf.Struct` — the DTO fields, by name.
    """

    def __init__(
        self,
        instances: Mapping[str, UseFramework] | None = None,
        layers: Iterable[str] = DEFAULT_LAYERS,
        title: str = "sincpro-grpc",
        version: str = "1.0.0",
    ):
        self._catalogs: dict[str, Catalog] = {}
        self._layers = tuple(layers)
        self._title = title
        self._version = version
        for alias, framework_instance in (instances or {}).items():
            self.add(alias, framework_instance)

    def add(
        self,
        alias: str,
        framework_instance: UseFramework,
        include: Iterable[type | str] | None = None,
        exclude: Iterable[type | str] | None = None,
        wrap: Mapping[type | str, Wrapper] | None = None,
    ) -> Self:
        """Register one instance under a package alias (`qr` → package `qr`)."""
        catalog = Catalog(framework_instance)
        if include is not None:
            catalog.include(*include)
        if exclude is not None:
            catalog.exclude(*exclude)
        for dto, wrapper in (wrap or {}).items():
            catalog.wrap(dto, wrapper)
        self._catalogs[proto.validate_package(alias)] = catalog
        return self

    def methods(self) -> dict[str, GrpcMethodSpec]:
        """Every served method keyed by its gRPC path."""
        return proto.index_specs(self._catalogs, self._layers)

    def describe(self) -> Scalar:
        """What `sincpro.Introspection/Describe` answers."""
        return proto.describe_document(self._title, self._version, self.methods())

    def proto_files(self) -> dict[str, str]:
        """`{filename: .proto source}` for the client teams' stub generation."""
        return proto.proto_files(self.methods())

    def write_proto_files(self, directory: str | Path) -> list[Path]:
        """Write the exported `.proto` files into `directory`, creating it if needed."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for filename, source in self.proto_files().items():
            path = target / filename
            path.write_text(source, encoding="utf-8")
            written.append(path)
        return written

    def handlers(self, health_check: Any | None = None) -> tuple[Any, ...]:
        """This gateway's `grpc.GenericRpcHandler`s — one per served service, plus
        `sincpro.Introspection` and, by default, `grpc.health.v1.Health`. No
        `grpc.Server` around them.

        `my_server.add_generic_rpc_handlers(gateway.handlers())` mounts this gateway's
        services on a server the caller built and owns — alongside another gateway's
        handlers on the same port, a hand-rolled executor, or `grpc.aio.server()`
        instead of the threaded one `.server()` always returns. `health_check` is a
        `Callable[[], bool]` re-evaluated on every `Check`/`Watch`; the default asks
        every registered `UseFramework` whether it is still built.
        """
        from sincpro_framework.entrypoints.grpc import wire

        specs = self.methods()
        document = proto.describe_document(self._title, self._version, specs)
        return tuple(wire.generic_handlers(specs, document, health_check))

    def server(
        self,
        max_workers: int = 10,
        interceptors: Iterable[Any] | None = None,
        options: Sequence[tuple[str, Any]] | None = None,
        reflection: bool = True,
        health_check: Any | None = None,
    ) -> Any:
        """A configured `grpc.Server` with no port bound and not started.

        Use it when the port, credentials or lifecycle are the caller's (a sidecar,
        a test, a process manager); `run()` is the one-liner for a plain service.
        `grpc.health.v1.Health` is served by default — pass `health_check=lambda: ...`
        for a deeper probe (a DB ping, a queue connection) than "did the bus build".
        """
        from sincpro_framework.entrypoints.grpc import wire

        specs = self.methods()
        return wire.build_server(
            specs,
            proto.describe_document(self._title, self._version, specs),
            max_workers=max_workers,
            interceptors=interceptors,
            options=options,
            reflection=reflection,
            health_check=health_check,
        )

    def run(
        self,
        address: str = DEFAULT_ADDRESS,
        credentials: Any | None = None,
        max_workers: int = 10,
        interceptors: Iterable[Any] | None = None,
        options: Sequence[tuple[str, Any]] | None = None,
        reflection: bool = True,
        health_check: Any | None = None,
        grace: float | None = 5.0,
        handle_signals: bool = True,
    ) -> None:
        """Serve until terminated, draining in-flight calls on SIGTERM/SIGINT.

        `credentials` is a `grpc.ServerCredentials`; without it the port is
        insecure, which is for localhost and for a mesh that terminates TLS in
        front of this process — never for a port exposed as it is.

        `handle_signals=True` (the default) hooks SIGTERM and SIGINT to
        `server.stop(grace)`: a rolling restart drains calls for up to `grace`
        seconds instead of the process being killed mid-request. Signals can only
        be hooked from the main thread — `run()` from a worker thread logs a
        warning and falls back to `wait_for_termination()` with no hook, same as
        `handle_signals=False`.
        """
        server = self.server(
            max_workers=max_workers,
            interceptors=interceptors,
            options=options,
            reflection=reflection,
            health_check=health_check,
        )
        if credentials is None:
            server.add_insecure_port(address)
        else:
            server.add_secure_port(address, credentials)
        server.start()
        logger.info("gRPC gateway [%s] serving on [%s]", self._title, address)
        if handle_signals:
            _hook_graceful_shutdown(server, self._title, grace)
        server.wait_for_termination()


def build_grpc_server(
    instances: Mapping[str, UseFramework],
    layers: Iterable[str] = DEFAULT_LAYERS,
    title: str = "sincpro-grpc",
    version: str = "1.0.0",
    **kwargs: Any,
) -> Any:
    return GrpcGateway(instances, layers=layers, title=title, version=version).server(
        **kwargs
    )
