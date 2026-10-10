"""Every failure of a FastAPI app as RFC 9457 problem details — `application/problem+json`, the
same kinds on every wire (PRD_15 §1.3), each with its HTTP status.

    app = FastAPI(separate_input_output_schemas=False)
    install_problem_handlers(app)

    {"type": "urn:sincpro:problem:not_found", "title": "Not found", "status": 404,
     "detail": "invoice F-9 does not exist", "instance": "/v1/billing/invoices/F-9",
     "kind": "not_found", "reason": "INVOICE_NOT_FOUND", "trace_id": "4bf9…"}

Context: a client switches on `kind` and `reason`, never on the text. A 500 says nothing of the
inside of the process — the log has it, once: what a bus already reported is not logged again.
FastAPI's own 422 (`RequestValidationError`) and Starlette's `HTTPException` (404, 405) answer
the same shape, and the OpenAPI document says so: FastAPI's `HTTPValidationError` is replaced by
`Problem`.
"""

import math
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping, Sequence
from http import HTTPStatus
from typing import Any

import starlette.exceptions
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from sincpro_framework.auth.domain.exceptions import (
    AuthError,
    PermissionDenied,
    Unauthenticated,
)
from sincpro_framework.auth.entrypoint.transports import challenges_of
from sincpro_framework.common.failures import (
    FailureKind,
    failure_reason,
    refined_failure_kind,
    retry_after,
    said_to_the_caller,
)
from sincpro_framework.entrypoints.adapters.rest.routing import InvalidRequest
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

STATUS_OF: Mapping[FailureKind, int] = {
    FailureKind.INVALID: 422,
    FailureKind.UNAUTHENTICATED: 401,
    FailureKind.PERMISSION_DENIED: 403,
    FailureKind.NOT_FOUND: 404,
    FailureKind.CONFLICT: 409,
    FailureKind.IN_PROGRESS: 409,
    FailureKind.KEY_REUSED: 422,
    FailureKind.DOMAIN: 422,
    FailureKind.EXHAUSTED: 429,
    FailureKind.UNAVAILABLE: 503,
    FailureKind.UNKNOWN_OUTCOME: 504,
    FailureKind.INTERNAL: 500,
}
"""The REST column of PRD_15 §1.3 — one row per kind, so no refusal turns into a crash."""

TITLE_OF: Mapping[FailureKind, str] = {
    FailureKind.INVALID: "The request does not validate",
    FailureKind.UNAUTHENTICATED: "Who is calling is not known",
    FailureKind.PERMISSION_DENIED: "Not allowed",
    FailureKind.NOT_FOUND: "Not found",
    FailureKind.CONFLICT: "The write collided",
    FailureKind.IN_PROGRESS: "The same request is still running",
    FailureKind.KEY_REUSED: "The idempotency key was used for another request",
    FailureKind.DOMAIN: "The domain refused the request",
    FailureKind.EXHAUSTED: "Too many requests",
    FailureKind.UNAVAILABLE: "Unavailable for now",
    FailureKind.UNKNOWN_OUTCOME: "It may have run — verify before retrying",
    FailureKind.INTERNAL: "Internal error",
}

KIND_OF_STATUS: Mapping[int, FailureKind] = {
    401: FailureKind.UNAUTHENTICATED,
    403: FailureKind.PERMISSION_DENIED,
    404: FailureKind.NOT_FOUND,
    409: FailureKind.CONFLICT,
    429: FailureKind.EXHAUSTED,
    503: FailureKind.UNAVAILABLE,
    504: FailureKind.UNKNOWN_OUTCOME,
}
"""The kind of an `HTTPException` a route or Starlette raised — by its status."""


class IdempotencyKeyMissing(Exception):
    """A use case that runs once, whose Command says nothing of what identifies it, called
    without an `Idempotency-Key` header — answered 400: the retry could not be told from a new
    request."""


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
    trace_id: str | None = None
    errors: list[ProblemError] = []


def _pointer(location: Sequence[Any]) -> str:
    parts = list(location)
    if parts and parts[0] in ("body", "query", "path", "header", "cookie"):
        parts = parts[1:]
    return "#/" + "/".join(str(one).replace("~", "~0").replace("/", "~1") for one in parts)


def _errors(errors: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [
        {"pointer": _pointer(one.get("loc", ())), "detail": str(one.get("msg", ""))}
        for one in errors
    ]


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
    buses = getattr(request.state, BUSES_STATE, None) or ()
    return challenges_of(buses) or ["Bearer"]


def _body(
    request: Request,
    kind: FailureKind,
    status: int,
    reason: str,
    detail: str | None,
    **extensions: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": f"urn:sincpro:problem:{kind.value}",
        "title": TITLE_OF[kind],
        "status": status,
        "instance": request.url.path,
        "kind": kind.value,
        "reason": reason,
        "trace_id": _trace_id(request),
    }
    if detail:
        body["detail"] = detail
    body.update({name: value for name, value in extensions.items() if value})
    return body


def problem_of(request: Request, error: Exception) -> JSONResponse:
    """The answer to `error`, raised anywhere a request is served.

    1. What could not be read — FastAPI's JSON error, a query string that is not JSON, a
       missing `Idempotency-Key` — is a 400; what does not validate a 422, each field pointed.
    2. An `HTTPException` keeps its status and headers (405 keeps `Allow`).
    3. Anything else is classified once (`refined_failure_kind`): its status, `Retry-After`
       when retrying helps, `WWW-Authenticate` on a 401.
    4. Final: a 500 says nothing; logged here unless a bus already reported it.
    """
    if isinstance(error, RequestValidationError):
        unreadable = any(one.get("type") == "json_invalid" for one in error.errors())
        status = 400 if unreadable else 422
        return _answer(
            _body(
                request,
                FailureKind.INVALID,
                status,
                "UNREADABLE" if unreadable else "INVALID",
                None,
                errors=_errors(error.errors()),
            )
        )
    if isinstance(error, InvalidRequest | IdempotencyKeyMissing):
        reason = "IDEMPOTENCY_KEY_MISSING" if isinstance(error, IdempotencyKeyMissing) else ""
        return _answer(
            _body(
                request, FailureKind.INVALID, 400, reason or "UNREADABLE", str(error) or None
            )
        )
    if isinstance(error, starlette.exceptions.HTTPException):
        status = error.status_code
        kind = KIND_OF_STATUS.get(
            status, FailureKind.INTERNAL if status >= 500 else FailureKind.INVALID
        )
        phrase = HTTPStatus(status).phrase if status in HTTPStatus._value2member_map_ else ""
        detail = error.detail if isinstance(error.detail, str) else None
        body = _body(
            request,
            kind,
            status,
            phrase.upper().replace(" ", "_").replace("-", "_") or kind.value.upper(),
            None if detail == phrase else detail,
        )
        body["title"] = phrase or body["title"]
        headers = dict(error.headers or {})
        if status == 401 and "www-authenticate" not in {one.lower() for one in headers}:
            headers["WWW-Authenticate"] = ", ".join(_challenges(request))
        return _answer(body, headers)
    kind = refined_failure_kind(error)
    status = STATUS_OF[kind]
    reason = failure_reason(error, kind)
    headers: dict[str, str] = {}
    wait = retry_after(error, kind)
    if wait is not None:
        headers["Retry-After"] = str(math.ceil(wait.total_seconds()))
    if isinstance(error, ValidationError):
        body = _body(
            request,
            kind,
            status,
            reason,
            None,
            errors=_errors(error.errors(include_url=False)),
        )
    elif isinstance(error, AuthError):
        body = _body(
            request,
            kind,
            status,
            reason,
            error.reason,
            step_up=error.step_up if isinstance(error, Unauthenticated) else None,
            requirement=error.requirement if isinstance(error, PermissionDenied) else None,
        )
    else:
        body = _body(request, kind, status, reason, said_to_the_caller(error))
    if status == 401:
        headers["WWW-Authenticate"] = ", ".join(_challenges(request))
    if kind == FailureKind.INTERNAL and not process.was_reported(error):
        logger.exception("REST %s %s failed", request.method, request.url.path)
    return _answer(body, headers)


def _answer(body: dict[str, Any], headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        body, status_code=body["status"], headers=dict(headers or {}), media_type=PROBLEM_JSON
    )


type Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
type Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
type Asgi = Callable[[MutableMapping[str, Any], Receive, Send], Awaitable[None]]


class ProblemMiddleware:
    """Answers, as a problem, what no exception handler did — a failure a bus raised, whatever
    its class — without letting it reach Starlette's server-error middleware, which logs it a
    second time and answers plain text.

    Context: pure ASGI, so a streamed response already started is left alone (re-raised)."""

    def __init__(self, app: Asgi) -> None:
        self.app = app

    async def __call__(
        self, scope: MutableMapping[str, Any], receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def sending(message: MutableMapping[str, Any]) -> None:
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
    `components/securitySchemes`, and a body without its path fields put in place. Running it twice changes nothing.
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
    return problem_of(request, error)


def install_problem_handlers(app: FastAPI) -> FastAPI:
    """Every failure of `app` answered as a problem, and its document saying so.

    1. `RequestValidationError` and Starlette's `HTTPException` — 404 and 405 too.
    2. `ProblemMiddleware` for anything else: a domain refusal, an auth refusal, an idempotency
       refusal, a 500.
    3. Final: `app.openapi` wrapped by `problem_document`.
    """
    app.add_exception_handler(RequestValidationError, _handled)
    app.add_exception_handler(starlette.exceptions.HTTPException, _handled)
    app.add_middleware(ProblemMiddleware)  # pyright: ignore[reportArgumentType]
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        return problem_document(generate())

    app.openapi = openapi  # pyright: ignore[reportAttributeAccessIssue]
    setattr(app.state, INSTALLED, True)
    return app


def problem_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {PROBLEM_JSON: {"schema": {"$ref": PROBLEM_REF}}},
    }
