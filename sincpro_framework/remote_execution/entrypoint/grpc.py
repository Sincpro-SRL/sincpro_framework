"""The gRPC Open Host: `/sincpro.Contexts/Execute`, answering what `grpc://` addresses send.

    GrpcGateway({"billing": billing}).run("0.0.0.0:50051")          # mounts it, with no change

Context: the handler every `GrpcGateway` mounts beside its `Struct` methods — those stay, for
clients that do not share the code. The execution is the one both Open Hosts share
(`entrypoint.execution.execute_hosted`); the wire is the caller's (`adapters.grpc`).
"""

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from sincpro_framework.entrypoints.grpc.wire import TRACE_HEADERS, grpc
from sincpro_framework.observability import process
from sincpro_framework.remote_execution.adapters.grpc import (
    CONTEXT_HEADER,
    DTO_HEADER,
    ERROR_KIND,
    ERROR_MODULE,
    METHOD,
    REQUEST_CONTEXT_HEADER,
    SERVICE,
)
from sincpro_framework.remote_execution.domain.payload import ChunkReader
from sincpro_framework.remote_execution.entrypoint.execution import execute_hosted
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework


def _header(metadata: Sequence[tuple[str, Any]], key: str) -> Any:
    return next((value for name, value in metadata if name == key), None)


def open_host_handler(contexts: Mapping[str, UseFramework]) -> Any:
    """`/sincpro.Contexts/Execute` for `contexts`, keyed by their names."""

    def execute(requests: Iterator[bytes], call: Any) -> Iterator[bytes]:
        from sincpro_framework.auth.transports import credentials_from_headers

        metadata = call.invocation_metadata()
        name = _header(metadata, CONTEXT_HEADER) or ""
        answer: Iterator[bytes] = iter(())
        try:
            answer = execute_hosted(
                contexts,
                name,
                _header(metadata, DTO_HEADER) or "",
                ChunkReader(requests),
                _header(metadata, REQUEST_CONTEXT_HEADER),
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
                )
            )
            call.abort(grpc.StatusCode.UNKNOWN, str(error))
        yield from answer

    return grpc.method_handlers_generic_handler(
        SERVICE, {METHOD: grpc.stream_stream_rpc_method_handler(execute)}
    )
