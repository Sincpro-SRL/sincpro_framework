"""What an HTTP host needs around its buses, in one line each: a JSON line and a span per
request, and the libraries' own logging in the same format, on the same trace.

    from sincpro_framework.observability.http import configure_process_logging, http_middleware

    configure_process_logging("INFO")        # uvicorn, httpx, starlette — one format, trace ids
    app = http_middleware(app)               # any ASGI app: FastAPI, Starlette, FastMCP's

Context: the bus already traces and logs every use case; what never reaches one — a 401 from
the host's own auth, a 413 from a body limit, a route that does not exist — is seen only here.
Each request leaves one line: method, path, status, duration and the size it declared, with the
trace ids; a refusal may carry fields of the host's own (`describe`), and a request that raised
before answering is an error with its traceback, reported to the error tracker. The query
string is never written: a credential may ride in it. The level follows the status — `info`,
`warning` from 400, `error` from 500 — and a path the probes hit every few seconds
(`/health`) is written only when it fails, and never traced.

With the `[opentelemetry]` extra, the request runs inside a server span — a child of the
`traceparent` it brought, unless the host says not to trust it (a gateway that starts traces of
its own) — so the buses' spans and every log line of the request land on its trace. Without it,
the line is still written, with no ids. Nothing here imports a web framework: it is ASGI.
"""

import logging
import sys
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any

import structlog

from sincpro_framework.observability.api import process
from sincpro_framework.observability.tracing.setup import current_otel_context
from sincpro_framework.sincpro_conf import settings
from sincpro_framework.sincpro_logger import create_logger

type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

QUIET_PATHS = ("/health",)
"""Paths written only when they fail — a probe hitting them every few seconds would bury the
rest."""

LIBRARY_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "httpx",
    "httpcore",
    "starlette",
    "fastapi",
    "fastmcp",
    "mcp",
)
"""The libraries an HTTP host runs whose logging joins the framework's format."""

INSTRUMENTATION = "sincpro_framework.http"

access_log = create_logger("sincpro_framework.http")
"""The logger of the request lines — stamped with the ids of the request being served."""
process.bind_logger(access_log)


def _with_trace_ids(
    _logger: Any, _method: str, event: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """`trace_id` and `span_id` of the active span on a line written by a library — a line of
    the framework's own loggers already carries them."""
    for key, value in current_otel_context().items():
        event.setdefault(key, value)
    return event


def instrument_httpx() -> bool:
    """Trace every httpx call — when `opentelemetry-instrumentation-httpx` is installed; once,
    however many times it is called. Answers whether httpx is traced.

    Context: the instrumentation takes OpenTelemetry's global tracer, a proxy that follows the
    provider the framework installs when a bus is built; a host that wants to be sure calls this
    again after `build_root_bus()`, and nothing is instrumented twice."""
    try:
        from opentelemetry.instrumentation.httpx import (  # pyright: ignore[reportMissingImports]
            HTTPXClientInstrumentor,
        )
    except ImportError:
        return False
    instrumentor = HTTPXClientInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument()
    return True


def configure_process_logging(
    level: str = "INFO", loggers: Iterable[str] = LIBRARY_LOGGERS
) -> None:
    """Every line the process writes in the framework's format, on the trace of the request —
    uvicorn's, httpx's, any library's through the standard `logging`.

        configure_process_logging("INFO")
        logging.getLogger("uvicorn.error").info("started")   ->  one JSON line, with trace ids

    1. The root logger writes where the framework's log writes — stdout (`print`) or its file
       (`file`) — through the processors the framework configured: the same renderer, so a
       change of format reaches both, with the trace ids added.
    2. Each of `loggers` drops handlers of its own and propagates to the root: uvicorn installs
       its own, and a line would otherwise be written twice.
    3. httpx is traced when its OpenTelemetry instrumentation is installed (`instrument_httpx`).
    Final: the root at `level`, nobody else's level lowered.

    Context: the backend is the one the framework was configured with — `print`, `file` or
    `stdlib`. With `stdlib` the framework already logs through the host's `logging` (Odoo): the
    root's handlers and format are the host's, and are left as they are; the libraries still
    propagate to them."""
    backend = settings.sincpro_framework_log_backend
    if backend != "stdlib":
        processors = structlog.get_config()["processors"]
        handler: logging.Handler = (
            logging.FileHandler(settings.sincpro_framework_log_file_path)
            if backend == "file" and settings.sincpro_framework_log_file_path
            else logging.StreamHandler(sys.stdout)
        )
        handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=processors[-1],
                foreign_pre_chain=[_with_trace_ids, *processors[:-1]],
            )
        )
        root = logging.getLogger()
        root.handlers = [handler]
        root.setLevel(level)
    for name in loggers:
        library = logging.getLogger(name)
        library.handlers = []
        library.propagate = True
    instrument_httpx()


type Describe = Callable[[Scope, int, bytes], Mapping[str, Any]]
"""What a host adds to the line of a refused request — given the scope, the status and the first
bytes of the answer; never a credential, which is the host's to leave out."""


@dataclass
class _Answer:
    """How one request was answered, as the middleware saw it."""

    status: int = 0
    body: bytes = b""
    """The first bytes of a refusal's body — for `describe`."""
    error: Exception | None = None
    """What escaped before anything was answered."""


def _headers(scope: Scope) -> dict[str, str]:
    """The request's headers as text, lower-cased — what a propagator reads."""
    return {
        name.decode("latin-1").lower(): value.decode("latin-1")
        for name, value in scope.get("headers", [])
    }


class AccessLog:
    """An ASGI middleware: one line and one span per HTTP request — see the module."""

    def __init__(
        self,
        app: ASGIApp,
        quiet_paths: Iterable[str] = QUIET_PATHS,
        trust_incoming_trace: bool = True,
        describe: Describe | None = None,
        body_bytes: int = 512,
    ) -> None:
        """`app` wrapped; `quiet_paths` written only when they fail, and never traced;
        `trust_incoming_trace` says whether a request's `traceparent` is its parent — `False`
        behind a gateway that starts traces of its own; `describe` adds fields of the host's own
        to the line of a request answered from 400 up, given the scope, the status and the first
        `body_bytes` of what was answered."""
        self.app = app
        self.quiet_paths = frozenset(quiet_paths)
        self.trust_incoming_trace = trust_incoming_trace
        self.describe = describe
        self.body_bytes = body_bytes

    def _logged(self, scope: Scope, answer: "_Answer", started: float) -> None:
        """The request's line, at the level its status says: method, path — never its query
        string —, status, duration, the size the request declared, and what `describe` adds to
        a refusal. A request that raised before answering is an error with its traceback,
        reported to the error tracker."""
        method, path, status = scope.get("method", ""), scope.get("path", ""), answer.status
        if path in self.quiet_paths and status < 400:
            return
        said = f"{method} {path} {status}"
        fields: dict[str, Any] = {
            "http_method": method,
            "http_path": path,
            "http_status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        declared = _headers(scope).get("content-length", "")
        if declared.isdigit():
            fields["request_bytes"] = int(declared)
        if self.describe is not None and status >= 400:
            try:
                fields.update(self.describe(scope, status, bytes(answer.body)))
            except Exception as error:
                fields["describe_failed"] = type(error).__name__
        if answer.error is not None:
            access_log.exception(said, **fields)
            process.record_error(answer.error, layer="asgi")
        elif status >= 500:
            access_log.error(said, **fields)
        elif status >= 400:
            access_log.warning(said, **fields)
        else:
            access_log.info(said, **fields)

    async def _served(
        self, scope: Scope, receive: Receive, send: Send, answer: "_Answer"
    ) -> None:
        """The request served — its status noted as it starts, the first `body_bytes` of a
        refusal kept, and what escaped before answering held — so the line is written whatever
        happened."""

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                answer.status = message["status"]
            elif (
                message["type"] == "http.response.body"
                and answer.status >= 400
                and len(answer.body) < self.body_bytes
            ):
                answer.body += message.get("body", b"")[: self.body_bytes - len(answer.body)]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        except Exception as error:
            if not answer.status:
                answer.status, answer.error = 500, error
            raise

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve the request inside its span, then write its line.

        1. Anything but HTTP — a lifespan, a websocket — passes untouched.
        2. A quiet path, or no tracer: served with no span.
        3. Else a server span, the child of the incoming `traceparent` when it is trusted, a
           root otherwise; the status code on it, an error status from 500.
        Final: the line, whether the request answered or raised — 500 when it raised before
        answering."""
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        answer = _Answer()
        tracer = process.tracer(INSTRUMENTATION)
        if tracer is None or scope.get("path", "") in self.quiet_paths:
            try:
                await self._served(scope, receive, send, answer)
            finally:
                self._logged(scope, answer, started)
            return
        from opentelemetry import context, propagate, trace

        parent = (
            propagate.extract(_headers(scope))
            if self.trust_incoming_trace
            else context.Context()
        )
        with tracer.start_as_current_span(
            f"{scope.get('method', '')} {scope.get('path', '')}",
            context=parent,
            kind=trace.SpanKind.SERVER,
            attributes={
                "http.request.method": scope.get("method", ""),
                "url.path": scope.get("path", ""),
            },
        ) as span:
            try:
                await self._served(scope, receive, send, answer)
            finally:
                status = answer.status or 500
                answer.status = status
                span.set_attribute("http.response.status_code", status)
                if status >= 500:
                    span.set_status(trace.Status(trace.StatusCode.ERROR))
                self._logged(scope, answer, started)  # inside the span: the line has its ids


def http_middleware(
    app: ASGIApp,
    quiet_paths: Iterable[str] = QUIET_PATHS,
    trust_incoming_trace: bool = True,
    describe: Describe | None = None,
    body_bytes: int = 512,
) -> AccessLog:
    """`app` with one JSON line and one span per request — the host's one line.

    app = http_middleware(FastApiGateway({...}).app())
    app = http_middleware(mcp.http_app(), trust_incoming_trace=False)    behind Kong
    app = http_middleware(app, describe=lambda scope, status, body: {"answered": body[:200]})
    """
    return AccessLog(app, quiet_paths, trust_incoming_trace, describe, body_bytes)
