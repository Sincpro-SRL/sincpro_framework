"""Every failure of a FastAPI app as RFC 9457 problem details — `application/problem+json`, the
same kinds on every wire (PRD_15 §1.3), each with its HTTP status.

    app = FastAPI(separate_input_output_schemas=False)
    install_problem_handlers(app)

    {"type": "urn:sincpro:problem:not_found", "title": "Not found", "status": 404,
     "detail": "invoice F-9 does not exist", "instance": "/v1/billing/invoices/F-9",
     "kind": "not_found", "reason": "INVOICE_NOT_FOUND", "next_step": "do_not_retry",
     "guidance": "What the request names does not exist: …", "retryable": false,
     "trace_id": "4bf9…"}

Context: the body is the `Problem` every wire tells (`common.failures.problem_of`) with the RFC 9457
members around it. A client switches on `kind`, `reason` and `next_step`, never on the text. A 500 says nothing of the
inside of the process — the log has it, once: what a bus already reported is not logged again.
FastAPI's own 422 (`RequestValidationError`) and Starlette's `HTTPException` (404, 405) answer
the same shape, and the OpenAPI document says so: FastAPI's `HTTPValidationError` is replaced by
`Problem`.
"""

import math
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from http import HTTPStatus
from typing import Any

import starlette.exceptions
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from starlette.requests import Request
from starlette.responses import JSONResponse

from sincpro_framework.auth import security
from sincpro_framework.common import failures
from sincpro_framework.entrypoints.adapters.fastapi import exceptions
from sincpro_framework.observability import process
from sincpro_framework.observability.tracing.propagation import trace_id_of
from sincpro_framework.sincpro_logger import logger

PROBLEM_JSON = "application/problem+json"
PROBLEM_REF = "#/components/schemas/Problem"
SCHEMES_EXTENSION = "x-sincpro-security-schemes"
"""Where an operation carries the security schemes it names until the document lifts them into
`components/securitySchemes` — see `install_problem_handlers`."""
BODY_EXTENSION = "x-sincpro-request-body"
"""The body schema of an operation whose path carries some of the DTO's fields — the DTO without
them, put in place of FastAPI's by `problem_document`."""
INSTALLED = "sincpro_problem_handlers"
BUSES_STATE = "sincpro_buses"
TRACE_STATE = "sincpro_trace_id"

KIND_OF_STATUS: Mapping[int, exceptions.FailureKind] = {
    401: exceptions.FailureKind.UNAUTHENTICATED,
    403: exceptions.FailureKind.PERMISSION_DENIED,
    404: exceptions.FailureKind.NOT_FOUND,
    409: exceptions.FailureKind.CONFLICT,
    429: exceptions.FailureKind.EXHAUSTED,
    503: exceptions.FailureKind.UNAVAILABLE,
    504: exceptions.FailureKind.UNKNOWN_OUTCOME,
}
"""The kind of an `HTTPException` a route or Starlette raised — by its status."""


class ProblemError(BaseModel):
    """One field the request got wrong — `pointer` addresses it in the DTO (`#/total`)."""

    pointer: str
    detail: str


class Problem(BaseModel):
    """RFC 9457 problem details, with the extensions every wire carries."""

    model_config = ConfigDict(extra="allow", title="Problem")

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    kind: str
    """The failure kind — the same word on every wire."""
    reason: str
    """UPPER_SNAKE, stable: what a client switches on."""
    next_step: str
    """What the caller does next — the same word on every wire (`NextStep`)."""
    guidance: str
    """What to do next, in words — for a person or an agent reading it."""
    retryable: bool
    retry_after: float | None = None
    """Seconds to wait before sending it again — as the `Retry-After` header says."""
    trace_id: str | None = None
    errors: list[ProblemError] = []


def _trace_id(request: Request) -> str | None:
    """The trace the failure belongs to: what the bus call saw, the caller's `traceparent`, or
    the host's active span."""
    seen = getattr(request.state, TRACE_STATE, None)
    if seen:
        return seen
    return trace_id_of(request.headers.get("traceparent")) or process.trace_ids().get(
        "trace_id"
    )


def _challenges(request: Request) -> list[str]:
    """The `WWW-Authenticate` challenges of a 401 — those of the buses the request called
    (noted by `bus_call`), `Bearer` when none says one."""
    buses = getattr(request.state, BUSES_STATE, None) or ()
    return security.challenges(buses) or ["Bearer"]


def _body(request: Request, problem: failures.Problem) -> dict[str, Any]:
    """A problem's body: the RFC 9457 members, the problem as every wire tells it, the trace, and
    what its error disclosed — a member with nothing to say is left out.

        _body(request, problem_of(InvoiceNotFound("F-9 does not exist")))
        ->  {"type": "urn:sincpro:problem:not_found", "title": "Not found", "status": 404,
             "instance": "/v1/billing/invoices/F-9", "kind": "not_found",
             "reason": "INVOICE_NOT_FOUND", "detail": "F-9 does not exist",
             "next_step": "do_not_retry", "guidance": "…", "retryable": false,
             "trace_id": "4bf9…"}
    """
    told = problem.model_dump(mode="json", exclude={"extensions"})
    body = {
        "type": f"urn:sincpro:problem:{problem.kind.value}",
        "instance": request.url.path,
        "trace_id": _trace_id(request),
        **told,
        **problem.extensions,
    }
    return {name: value for name, value in body.items() if value not in (None, [], {}, "")}


def _answer(body: dict[str, Any], headers: Mapping[str, str] | None = None) -> JSONResponse:
    """The problem as a response — its own status, `application/problem+json`."""
    return JSONResponse(
        body, status_code=body["status"], headers=dict(headers or {}), media_type=PROBLEM_JSON
    )


def problem_of(request: Request, error: Exception) -> JSONResponse:
    """The answer to `error`, raised anywhere a request is served.

        problem_of(request, InvoiceNotFound("F-9 does not exist"))
        ->  404 {"kind": "not_found", "reason": "INVOICE_NOT_FOUND",
                 "detail": "F-9 does not exist", ...}
        problem_of(request, HTTPException(405, headers={"Allow": "GET"}))
        ->  405 {"title": "Method Not Allowed", "reason": "METHOD_NOT_ALLOWED", ...}, Allow
        problem_of(request, RuntimeError("postgres://admin:pw@db"))
        ->  500 {"kind": "internal", ...} — no detail; logged here

    1. What could not be read — FastAPI's JSON error, a query string that is not JSON, a
       missing `Idempotency-Key` — is a 400; what does not validate a 422, each field pointed.
    2. An `HTTPException` is answered with its own status and headers (a 405 still carries `Allow`).
    3. Anything else is classified once (`refined_failure_kind`): its status, `Retry-After`
       when retrying helps, `WWW-Authenticate` on a 401.
    4. Final: a 500 says nothing; logged here unless a bus already reported it.
    """
    if isinstance(error, RequestValidationError):
        unreadable = any(one.get("type") == "json_invalid" for one in error.errors())
        problem = failures.Problem.for_kind(
            exceptions.FailureKind.INVALID,
            "UNREADABLE" if unreadable else "INVALID",
            None,
            [
                failures.FieldError(
                    pointer=failures.pointer(one.get("loc", ())),
                    detail=str(one.get("msg", "")),
                )
                for one in error.errors()
            ],
        )
        return _answer(
            _body(request, problem.model_copy(update={"status": 400 if unreadable else 422}))
        )
    if isinstance(error, exceptions.InvalidRequest | exceptions.IdempotencyKeyMissing):
        reason = (
            "IDEMPOTENCY_KEY_MISSING"
            if isinstance(error, exceptions.IdempotencyKeyMissing)
            else "UNREADABLE"
        )
        problem = failures.Problem.for_kind(
            exceptions.FailureKind.INVALID, reason, str(error) or None
        )
        return _answer(_body(request, problem.model_copy(update={"status": 400})))
    if isinstance(error, starlette.exceptions.HTTPException):
        status = error.status_code
        kind = KIND_OF_STATUS.get(
            status,
            (
                exceptions.FailureKind.INTERNAL
                if status >= 500
                else exceptions.FailureKind.INVALID
            ),
        )
        phrase = HTTPStatus(status).phrase if status in HTTPStatus._value2member_map_ else ""
        detail = error.detail if isinstance(error.detail, str) else None
        problem = failures.Problem.for_kind(
            kind,
            phrase.upper().replace(" ", "_").replace("-", "_") or kind.value.upper(),
            None if detail == phrase else detail,
        )
        problem = problem.model_copy(
            update={"status": status, "title": phrase or problem.title}
        )
        headers = dict(error.headers or {})
        if status == 401 and "www-authenticate" not in {one.lower() for one in headers}:
            headers["WWW-Authenticate"] = ", ".join(_challenges(request))
        return _answer(_body(request, problem), headers)
    problem = failures.problem_of(error)
    headers: dict[str, str] = {}
    if problem.retry_after is not None:
        headers["Retry-After"] = str(math.ceil(problem.retry_after.total_seconds()))
    if problem.status == 401:
        headers["WWW-Authenticate"] = ", ".join(_challenges(request))
    if problem.kind == exceptions.FailureKind.INTERNAL and not process.was_reported(error):
        logger.exception("REST %s %s failed", request.method, request.url.path)
    return _answer(_body(request, problem), headers)


type Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
type Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
type Asgi = Callable[[MutableMapping[str, Any], Receive, Send], Awaitable[None]]


class ProblemMiddleware:
    """Answers, as a problem, what no exception handler did — a failure a bus raised, whatever
    its class — without letting it reach Starlette's server-error middleware, which logs it a
    second time and answers plain text.

    Context: pure ASGI, so a streamed response already started is left alone (re-raised)."""

    def __init__(self, app: Asgi) -> None:
        """Wrap `app` — what Starlette hands a middleware added with `add_middleware`."""
        self.app = app

    async def __call__(
        self, scope: MutableMapping[str, Any], receive: Receive, send: Send
    ) -> None:
        """Serve one ASGI call, answering a failure as a problem while nothing was sent yet.

        1. Anything but HTTP (lifespan, websocket) passes through untouched.
        2. `send` is watched: once the response has started, a failure is re-raised — a
           second start would break the protocol.
        Final: a failure before that answers `problem_of(request, error)`.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def sending(message: MutableMapping[str, Any]) -> None:
            """`send`, noting when the response starts."""
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, sending)
        except Exception as error:
            if started:
                raise
            await problem_of(Request(scope), error)(scope, receive, send)


def _referenced(node: Any) -> set[str]:
    """The name of every schema a `$ref` points at anywhere in `node`, nested lists and dicts
    walked.

        _referenced({"a": {"$ref": "#/components/schemas/Problem"}, "b": [{"$ref": "#/X"}]})
        ->  {"Problem", "X"}
    """
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.add(value.rsplit("/", 1)[-1])
            else:
                found |= _referenced(value)
    elif isinstance(node, list):
        for one in node:
            found |= _referenced(one)
    return found


def problem_document(document: dict[str, Any]) -> dict[str, Any]:
    """The document with `Problem` in its components, every response FastAPI described with its
    `HTTPValidationError` answered as a problem, each operation's security schemes lifted into
    `components/securitySchemes`, and a body without its path fields put in place. Running it
    twice changes nothing.

        problem_document({"paths": {"/invoices": {"post": {"responses": {"422":
            {"content": {"application/json": {"schema":
                {"$ref": "#/components/schemas/HTTPValidationError"}}}}}}}}})
        ->  the same, its 422 as {"application/problem+json": {"schema":
            {"$ref": "#/components/schemas/Problem"}}}, `Problem` in components/schemas

    1. `Problem` and the models it nests into `components/schemas`.
    2. Per operation:
       2.1 its `x-sincpro-security-schemes` popped and gathered;
       2.2 its `x-sincpro-request-body` put in place of FastAPI's body schema;
       2.3 a response FastAPI described with `HTTPValidationError` answered as
           `application/problem+json`.
    3. The gathered schemes into `components/securitySchemes`.
    Final: `HTTPValidationError` and `ValidationError` dropped when nothing points at them.
    """
    components = document.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    shaped = Problem.model_json_schema(ref_template="#/components/schemas/{model}")
    for name, definition in shaped.pop("$defs", {}).items():
        schemas.setdefault(name, definition)
    schemas.setdefault("Problem", shaped)
    schemes: dict[str, Any] = {}
    for item in document.get("paths", {}).values():
        for operation in item.values():
            if not isinstance(operation, dict):
                continue
            schemes.update(operation.pop(SCHEMES_EXTENSION, {}))
            body = operation.pop(BODY_EXTENSION, None)
            if body is not None:
                operation["requestBody"]["content"]["application/json"]["schema"] = body
            for answer in (operation.get("responses") or {}).values():
                content = answer.get("content") or {}
                described = content.get("application/json", {}).get("schema", {})
                if described.get("$ref", "").endswith("/HTTPValidationError"):
                    del content["application/json"]
                    content[PROBLEM_JSON] = {"schema": {"$ref": PROBLEM_REF}}
    if schemes:
        components.setdefault("securitySchemes", {}).update(schemes)
    used = _referenced(document.get("paths", {})) | _referenced(
        {name: one for name, one in schemas.items() if name != "HTTPValidationError"}
    )
    for name in ("HTTPValidationError", "ValidationError"):
        if name in schemas and name not in used:
            del schemas[name]
    return document


async def _handled(request: Request, error: Exception) -> JSONResponse:
    """`problem_of` as a FastAPI exception handler — which must be async."""
    return problem_of(request, error)


def install_problem_handlers(app: FastAPI) -> FastAPI:
    """Every failure of `app` answered as a problem, and its document saying so.

        install_problem_handlers(FastAPI())   ->  the same app, problems answered
        GET /nowhere                          ->  404 application/problem+json

    1. `RequestValidationError` and Starlette's `HTTPException` — 404 and 405 too.
    2. `ProblemMiddleware` for anything else: a domain refusal, an auth refusal, an idempotency
       refusal, a 500.
    3. `app.openapi` wrapped by `problem_document`.
    Final: the app marked as installed — what `FastApiGateway.verify` checks.
    """
    app.add_exception_handler(RequestValidationError, _handled)
    app.add_exception_handler(starlette.exceptions.HTTPException, _handled)
    app.add_middleware(ProblemMiddleware)  # pyright: ignore[reportArgumentType]
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        """FastAPI's document, then `problem_document` over it."""
        return problem_document(generate())

    app.openapi = openapi  # pyright: ignore[reportAttributeAccessIssue]
    setattr(app.state, INSTALLED, True)
    return app


def problem_response(description: str) -> dict[str, Any]:
    """One entry of an operation's `responses=`: `description`, answered as a `Problem`.

        problem_response("Not found")
        ->  {"description": "Not found",
             "content": {"application/problem+json":
                         {"schema": {"$ref": "#/components/schemas/Problem"}}}}

    The schema is referenced, so a document carries `Problem` once.
    """
    return {
        "description": description,
        "content": {PROBLEM_JSON: {"schema": {"$ref": PROBLEM_REF}}},
    }
