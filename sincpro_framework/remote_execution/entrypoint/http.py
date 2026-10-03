"""The HTTP host of bounded contexts: one route to mount on the ASGI app a service already has.

    app.router.routes.extend(open_host_routes([catalog, execution]))      # FastAPI
    Starlette(routes=[*my_routes, *open_host_routes([catalog])])          # Starlette

Context: `POST /sincpro/contexts/execute` answers what `http://` addresses in a context map send
(`remote_execution.adapters.http`) — on the same address, port, ingress and TLS as
the service's REST API, so hosting a context needs no second server. The execution runs on a
worker thread, as the bus is synchronous — the same one the gRPC host runs
(`entrypoint.execution.execute_hosted`). The body is read as it arrives and the answer is streamed
back, so neither is ever one buffer.
"""

import asyncio
import base64
from collections.abc import AsyncIterator, Iterator, Sequence

from sincpro_framework.observability import process
from sincpro_framework.remote_execution.adapters.http import (
    CONTEXT_HEADER,
    DTO_HEADER,
    ERROR_DETAILS_HEADER,
    ERROR_KIND_HEADER,
    ERROR_MODULE_HEADER,
    PATH,
    REQUEST_CONTEXT_HEADER,
)
from sincpro_framework.remote_execution.domain.errors import error_details
from sincpro_framework.remote_execution.domain.payload import ChunkReader
from sincpro_framework.remote_execution.entrypoint.execution import execute_hosted
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework

RPC_MISSING = "Starlette is not installed. Install with: pip install sincpro-framework[rpc]"
TRACE_HEADERS = ("traceparent", "tracestate")

try:
    from starlette.requests import Request
    from starlette.responses import Response, StreamingResponse
    from starlette.routing import Route
except ImportError as error:  # pragma: no cover - depends on the installed extra
    raise ImportError(RPC_MISSING) from error


def _read_from(
    stream: AsyncIterator[bytes], loop: asyncio.AbstractEventLoop
) -> Iterator[bytes]:
    """The request body's chunks, pulled from a worker thread off the loop that receives them."""

    async def following() -> bytes | None:
        try:
            return await anext(stream)
        except StopAsyncIteration:
            return None

    while (chunk := asyncio.run_coroutine_threadsafe(following(), loop).result()) is not None:
        yield chunk


def open_host_routes(contexts: Sequence[UseFramework]) -> list[Route]:
    """The route that hosts `contexts` for calling services, keyed by their names — they run in
    this process, whatever the context map says.

    1. The context, the DTO and the request context from the headers; the DTO's values from the
       body, read as it arrives.
    2. Executed on a worker thread (`execute_hosted`).
    3. Final: 200 streaming the answer's values; 404 when this service does not host the
       context; 500 naming the raised class and its details in headers, its message as the body.
    """
    by_name = {one.name: one for one in contexts}
    for bus in contexts:
        bus.run_here()

    async def execute(request: Request) -> Response:
        from sincpro_framework.auth.transports import credentials_from_asgi

        name = request.headers.get(CONTEXT_HEADER, "")
        packed = request.headers.get(REQUEST_CONTEXT_HEADER)
        carrier = {
            key: request.headers[key] for key in TRACE_HEADERS if key in request.headers
        }
        body = ChunkReader(_read_from(request.stream(), asyncio.get_running_loop()))
        try:
            answer = await asyncio.to_thread(
                execute_hosted,
                by_name,
                name,
                request.headers.get(DTO_HEADER, ""),
                body,
                base64.b64decode(packed) if packed else None,
                carrier,
                credentials_from_asgi(request.scope, "service"),
            )
        except LookupError as error:
            return Response(str(error), status_code=404, media_type="text/plain")
        except Exception as error:
            if not process.was_reported(error):
                logger.exception("execution for a calling service failed on %s", name)
            return Response(
                str(error),
                status_code=500,
                media_type="text/plain",
                headers={
                    ERROR_MODULE_HEADER: type(error).__module__,
                    ERROR_KIND_HEADER: type(error).__qualname__,
                    ERROR_DETAILS_HEADER: base64.b64encode(error_details(error)).decode(),
                },
            )
        return StreamingResponse(answer, media_type="application/octet-stream")

    return [Route(PATH, execute, methods=["POST"])]
