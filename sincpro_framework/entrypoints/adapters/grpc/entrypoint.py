"""gRPC gateway: the declared surface of one or more buses as AIP-named gRPC services (PRD_15 §3).

    GrpcGateway([billing])                                   # only what is @grpc()-declared
    GrpcGateway([billing], exposure=Exposure.CATALOG)        # every use case, same names
    GrpcGateway([billing]).group(billing, version="v2")      # /billing.v2.BillingService/...

Three ways to use it: `.server()`/`.run()` build and serve everything; `.handlers()` hands the
generic handlers to a server the project owns; full control is a hand-written servicer calling
`bus_call` on a server built with `framework_interceptors()`, and `.mount(server,
reflection_extra=[...])` adding the generated services and one reflection over both.
Remote execution's internal door is never here (PRD_15 §0).
"""

import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from sincpro_framework.entrypoints.adapters.grpc import naming, proto
from sincpro_framework.entrypoints.adapters.grpc.naming import GrpcWire
from sincpro_framework.entrypoints.adapters.grpc.proto import (
    MAX_CONNECTION_AGE,
    MAX_MESSAGE_BYTES,
    GrpcMethodSpec,
)
from sincpro_framework.entrypoints.domain.layers import Scalar
from sincpro_framework.entrypoints.domain.surface import Exposure
from sincpro_framework.entrypoints.entrypoint.gateway import DEFAULT_LAYERS, Buses, Gateway
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework

DEFAULT_ADDRESS = "127.0.0.1:50051"
NOTHING_PUBLISHED = (
    "gRPC gateway [%s] publishes no method: declare @grpc() on a use case, bind it, or build "
    "the gateway with exposure=Exposure.CATALOG"
)


def bus_call[R](
    context: Any,
    bus: UseFramework,
    dto: DataTransferObject | Callable[[], DataTransferObject],
    to_response: Callable[[Any], R] | None = None,
    domain: str | None = None,
) -> Any:
    """A hand-written servicer's call to the bus, on the exact path a generated method takes:
    metadata → context, the caller's credentials, the deadline pre-check, `bus(dto)`, a
    `google.rpc.Status` on failure (`wire.bus_call`)."""
    from sincpro_framework.entrypoints.adapters.grpc import wire

    return wire.bus_call(context, bus, dto, to_response, domain=domain)


def framework_interceptors() -> list[Any]:
    """The deadline pre-check and a call log for a project's own `grpc.server(...)` — never
    auth: the bus guards itself (`wire.framework_interceptors`)."""
    from sincpro_framework.entrypoints.adapters.grpc import wire

    return wire.framework_interceptors()


class _DrainsFirst:
    """What the SIGTERM hook stops: health flips to NOT_SERVING, then the server drains."""

    def __init__(self, gateway: "GrpcGateway", server: Any):
        self.gateway = gateway
        self.server = server

    def stop(self, grace: float | None) -> Any:
        self.gateway.drain()
        return self.server.stop(grace)


class GrpcGateway(Gateway):
    """gRPC facade over one or more UseFramework instances.

    Every method is unary and takes/returns `google.protobuf.Struct` — the DTO fields, by name.
    `exposure` is `Exposure.DECLARED` by default: only the use cases bound for gRPC; `unguarded`
    says its buses have no `AccessControl` on purpose; `port` a `GrpcWire` of the project's
    that names differently.
    """

    wire = "grpc"

    def __init__(
        self,
        instances: Buses | None = None,
        layers: Iterable[str] = DEFAULT_LAYERS,
        title: str = "sincpro-grpc",
        version: str = "1.0.0",
        exposure: Exposure = Exposure.DECLARED,
        unguarded: bool = False,
        port: GrpcWire | None = None,
    ):
        self._draining = threading.Event()
        super().__init__(
            instances,
            layers,
            title,
            version,
            exposure=exposure,
            unguarded=unguarded,
            port=port or GrpcWire(),
        )

    def validate_alias(self, alias: str) -> str:
        if not naming.IDENTIFIER.match(alias):
            raise ValueError(
                f"gRPC instance alias [{alias}] must match {naming.IDENTIFIER.pattern} "
                "— a proto package segment allows no hyphen and no dot"
            )
        return alias

    def alias_for(self, framework_instance: UseFramework) -> str:
        return naming.alias_of(framework_instance.name)

    def added(self, alias: str, framework_instance: UseFramework) -> None:
        if framework_instance.hosted_at is not None:
            logger.warning(
                f"{framework_instance.name} is published here and the context map hosts it at "
                f"{framework_instance.hosted_at.address}: this gateway's calls are executed "
                "there. Hosting it for calling services is remote execution's door — "
                "bus.serve(...) or open_host([...]).mount(server) — never this gateway's"
            )

    def methods(self) -> dict[str, GrpcMethodSpec]:
        """Every served method keyed by its gRPC path — the validated surface, built. Refused
        with `ProgrammingError`, every reason listed, when it does not hold."""
        return self.build()

    def describe(self) -> Scalar:
        """What `sincpro.Introspection/Describe` answers."""
        return proto.describe_document(self._title, self._version, self.methods())

    def proto_files(self) -> dict[str, str]:
        """`{path: .proto source}` — `billing/v1/billing.proto` per package, for client stubs."""
        return proto.proto_files(self.methods())

    def write_proto_files(self, directory: str | Path) -> list[Path]:
        """Write the exported `.proto` files under `directory`, creating the package
        directories."""
        written: list[Path] = []
        for filename, source in self.proto_files().items():
            path = Path(directory) / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
            written.append(path)
        return written

    def drain(self) -> None:
        """Every service this gateway answers for turns NOT_SERVING in `grpc.health.v1` — what
        `run()` does on SIGTERM before `stop(grace)`, for a caller owning its own lifecycle.
        """
        self._draining.set()

    def handlers(self, health_check: Callable[[], bool] | None = None) -> tuple[Any, ...]:
        """This gateway's `grpc.GenericRpcHandler`s — one per served service, plus
        `sincpro.Introspection` and, by default, `grpc.health.v1.Health`. No `grpc.Server`
        around them, no reflection (`mount` adds it), never remote execution's door.

        `health_check` is re-evaluated on every `Check`/`Watch`; the default asks every
        published `UseFramework` whether it is still built. After `drain()` every service
        answers NOT_SERVING.
        """
        from sincpro_framework.entrypoints.adapters.grpc import wire

        specs = self.methods()
        if not specs:
            logger.warning(NOTHING_PUBLISHED, self._title)
        is_ready = wire.Readiness(
            health_check or wire.default_health_check(specs), self._draining
        )
        document = proto.describe_document(self._title, self._version, specs)
        return tuple(wire.generic_handlers(specs, document, is_ready))

    def mount(
        self,
        server: Any,
        reflection_extra: Sequence[Any] = (),
        reflection: bool = True,
        health_check: Callable[[], bool] | None = None,
    ) -> Any:
        """Add this gateway's services to a `grpc.Server` the project built, with one server
        reflection listing them and the hand-written services of `reflection_extra` — compiled
        `*_pb2.DESCRIPTOR` files.

        A hand-written method on a path this gateway serves is refused with `ProgrammingError`
        before anything is mounted. Answers `server`.
        """
        from sincpro_framework.entrypoints.adapters.grpc import wire

        specs = self.methods()
        wire.refuse_clashes(specs, reflection_extra)
        server.add_generic_rpc_handlers(self.handlers(health_check))
        if reflection:
            found = wire.reflection_handlers(specs, reflection_extra)
            if found:
                server.add_generic_rpc_handlers(found)
        return server

    def server(
        self,
        max_workers: int = 10,
        interceptors: Iterable[Any] | None = None,
        options: Sequence[tuple[str, Any]] | None = None,
        reflection: bool = True,
        health_check: Callable[[], bool] | None = None,
        maximum_concurrent_rpcs: int | None = None,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
        max_connection_age: float | None = MAX_CONNECTION_AGE,
    ) -> Any:
        """A configured `grpc.Server` with no port bound and not started.

        Past `maximum_concurrent_rpcs` calls at once (2 × `max_workers` by default) a call is
        answered RESOURCE_EXHAUSTED. A message over `max_message_bytes` (4 MiB), either way, is
        refused RESOURCE_EXHAUSTED; a connection is closed gracefully after
        `max_connection_age` seconds so a load balancer rebalances; keepalive pings detect dead
        peers. `options` replaces any of them by key (`wire.server_options`).
        """
        from sincpro_framework.common.transport.grpc import server
        from sincpro_framework.entrypoints.adapters.grpc import wire

        built = server(
            max_workers,
            interceptors,
            wire.server_options(options, max_message_bytes, max_connection_age),
            maximum_concurrent_rpcs,
        )
        return self.mount(built, reflection=reflection, health_check=health_check)

    def run(
        self,
        address: str = DEFAULT_ADDRESS,
        credentials: Any | None = None,
        max_workers: int = 10,
        interceptors: Iterable[Any] | None = None,
        options: Sequence[tuple[str, Any]] | None = None,
        reflection: bool = True,
        health_check: Callable[[], bool] | None = None,
        grace: float | None = 5.0,
        handle_signals: bool = True,
        maximum_concurrent_rpcs: int | None = None,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
        max_connection_age: float | None = MAX_CONNECTION_AGE,
    ) -> None:
        """Serve until terminated. On SIGTERM/SIGINT every service turns NOT_SERVING in health,
        then in-flight calls drain for up to `grace` seconds.

        `credentials` is a `grpc.ServerCredentials`; without it the port is insecure — for
        localhost and a mesh terminating TLS in front, never for a port exposed as it is.
        Signals can only be hooked from the main thread: off it, a warning and no hook, as with
        `handle_signals=False`.
        """
        from sincpro_framework.common.transport.grpc import hook_graceful_shutdown

        server = self.server(
            max_workers=max_workers,
            interceptors=interceptors,
            options=options,
            reflection=reflection,
            health_check=health_check,
            maximum_concurrent_rpcs=maximum_concurrent_rpcs,
            max_message_bytes=max_message_bytes,
            max_connection_age=max_connection_age,
        )
        if credentials is None:
            server.add_insecure_port(address)
        else:
            server.add_secure_port(address, credentials)
        logger.info("gRPC gateway [%s] serving on [%s]", self._title, address)
        server.start()
        if handle_signals:
            hook_graceful_shutdown(_DrainsFirst(self, server), self._title, grace)
        server.wait_for_termination()


def build_grpc_server(
    instances: Mapping[str, UseFramework],
    layers: Iterable[str] = DEFAULT_LAYERS,
    title: str = "sincpro-grpc",
    version: str = "1.0.0",
    exposure: Exposure = Exposure.DECLARED,
    unguarded: bool = False,
    **kwargs: Any,
) -> Any:
    return GrpcGateway(
        instances,
        layers=layers,
        title=title,
        version=version,
        exposure=exposure,
        unguarded=unguarded,
    ).server(**kwargs)
