"""The HTTP host of bounded contexts: one route to mount on the ASGI app a service already has.

    app.router.routes.extend(open_host_routes([catalog, execution]))      # FastAPI
    Starlette(routes=[*my_routes, *open_host_routes([catalog])])          # Starlette

Context: `POST /sincpro/contexts/execute` answers what `http://` addresses in a context map send
(`remote_execution.adapters.http`) — on the same address, port, ingress and TLS as
the service's REST API, so hosting a context needs no second server. The execution runs on a
worker thread, as the bus is synchronous — the same one the gRPC host runs
(`entrypoint.execution.execute_hosted`). The body and the answer are each one message
(`sincpro_framework.remote_execution.domain.payload`).
"""

import asyncio
import base64
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sincpro_framework.observability import process
from sincpro_framework.observability.tracing.propagation import TRACE_HEADERS
from sincpro_framework.remote_execution.adapters.http import (
    CONTEXT_HEADER,
    ERROR_DETAILS_HEADER,
    ERROR_KIND_HEADER,
    ERROR_MODULE_HEADER,
    PATH,
)
from sincpro_framework.remote_execution.domain.errors import error_details
from sincpro_framework.remote_execution.entrypoint.execution import (
    execute_hosted,
)
from sincpro_framework.sincpro_logger import logger

RPC_MISSING = "Starlette is not installed. Install with: pip install sincpro-framework[rpc]"

try:
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Route
except ImportError as error:  # pragma: no cover - depends on the installed extra
    raise ImportError(RPC_MISSING) from error

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework


def open_host_routes(contexts: "Sequence[UseFramework]") -> list[Route]:
    """The route that hosts `contexts` for calling services, keyed by their names — they run in
    this process, whatever the context map says.

    1. The context from its header; the message — the DTO's identity, its values, the request
       context — from the body.
    2. Executed on a worker thread (`execute_hosted`).
    3. Final: 200 with the answer's message; 404 when this service does not host the context;
       500 naming the raised class and its details in headers, its message as the body.
    """
    by_name = {one.name: one for one in contexts}
    for bus in contexts:
        bus.run_here()

    async def execute(request: Request) -> Response:
        from sincpro_framework.auth.entrypoint.transports import credentials_from_asgi

        name = request.headers.get(CONTEXT_HEADER, "")
        carrier = {
            key: request.headers[key] for key in TRACE_HEADERS if key in request.headers
        }
        body = await request.body()
        try:
            answer = await asyncio.to_thread(
                execute_hosted,
                by_name,
                name,
                body,
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
        return Response(answer, media_type="application/json")

    return [Route(PATH, execute, methods=["POST"])]
