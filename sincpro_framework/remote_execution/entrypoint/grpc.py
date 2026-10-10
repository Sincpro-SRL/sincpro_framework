"""The gRPC Open Host: `/sincpro.Contexts/Execute`, answering what `grpc://` addresses send.

    billing.serve("0.0.0.0:50051")                      # its own server: the open host and health
    open_host([billing]).mount(server)                  # or on a server of the caller's, on purpose

Context: the internal door — the project's own services reaching a context that runs here. It is
never mounted as a side effect: a public `GrpcGateway` publishes its declared surface only, and a
deployment that wants both on one port mounts this one explicitly. The execution is the one both
Open Hosts share (`entrypoint.execution.execute_hosted`); the wire is the caller's
(`adapters.grpc`).
"""

from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sincpro_framework.common.transport.grpc import (
    TRACE_HEADERS,
    grpc,
    health_handler,
    server,
)
from sincpro_framework.observability import process
from sincpro_framework.remote_execution.adapters.grpc import (
    ACCEPTED_HEADER,
    CONTEXT_HEADER,
    ERROR_DETAILS,
    ERROR_KIND,
    ERROR_MODULE,
    METHOD,
    SERVICE,
    chunks_of,
)
from sincpro_framework.remote_execution.domain.errors import error_details
from sincpro_framework.remote_execution.entrypoint.execution import (
    execute_hosted,
)
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework


def _header(metadata: Sequence[tuple[str, Any]], key: str) -> Any:
    return next((value for name, value in metadata if name == key), None)


def open_host_handler(contexts: "Mapping[str, UseFramework]") -> Any:
    """`/sincpro.Contexts/Execute` for `contexts`, keyed by their names."""

    def execute(requests: Iterator[bytes], call: Any) -> Iterator[bytes]:
        from sincpro_framework.auth.entrypoint.transports import credentials_from_headers

        call.send_initial_metadata(((ACCEPTED_HEADER, "1"),))
        metadata = call.invocation_metadata()
        name = _header(metadata, CONTEXT_HEADER) or ""
        answer = b""
        try:
            answer = execute_hosted(
                contexts,
                name,
                b"".join(requests),
                {key: value for key, value in metadata if key in TRACE_HEADERS},
                credentials_from_headers("service", metadata),
            )
        except LookupError as error:
            call.abort(grpc.StatusCode.NOT_FOUND, str(error))
        except Exception as error:
            if not process.was_reported(error):
                logger.exception("execution for a calling service failed on %s", name)
            call.set_trailing_metadata(
                (
                    (ERROR_MODULE, type(error).__module__),
                    (ERROR_KIND, type(error).__qualname__),
                    (ERROR_DETAILS, error_details(error)),
                )
            )
            call.abort(grpc.StatusCode.UNKNOWN, str(error))
        yield from chunks_of(answer)

    return grpc.method_handlers_generic_handler(
        SERVICE, {METHOD: grpc.stream_stream_rpc_method_handler(execute)}
    )


class OpenHostDoor:
    """The internal door for `contexts`, to mount on a gRPC server — its own, or one shared with
    a public gateway on purpose. The contexts it hosts run in this process, whatever the context
    map says."""

    def __init__(
        self, contexts: "Sequence[UseFramework] | Mapping[str, UseFramework]"
    ) -> None:
        self.contexts: "dict[str, UseFramework]" = (
            dict(contexts)
            if isinstance(contexts, Mapping)
            else {one.name: one for one in contexts}
        )
        for bus in self.contexts.values():
            bus.run_here()

    def is_ready(self) -> bool:
        return all(one.is_ready for one in self.contexts.values())

    def handlers(self, health: bool = True) -> tuple[Any, ...]:
        """`sincpro.Contexts/Execute` and, with `health`, `grpc.health.v1.Health` for it."""
        found: list[Any] = [open_host_handler(self.contexts)]
        probe = health_handler([SERVICE], self.is_ready) if health else None
        if probe is not None:
            found.append(probe)
        return tuple(found)

    def mount(self, server: Any, health: bool = False) -> Any:
        """Add the door to `server` — without its own health service by default, the server
        having one already when it is a gateway's."""
        server.add_generic_rpc_handlers(self.handlers(health=health))
        return server

    def server(self, max_workers: int = 10) -> Any:
        """A server of its own: the door and its health, nothing else — no public method, no
        introspection, no reflection. Not started, no port."""
        for bus in self.contexts.values():
            bus.build_root_bus()
        return self.mount(server(max_workers), health=True)


def open_host(
    contexts: "Sequence[UseFramework] | Mapping[str, UseFramework]",
) -> OpenHostDoor:
    """The internal door for `contexts` — see `OpenHostDoor`."""
    return OpenHostDoor(contexts)
