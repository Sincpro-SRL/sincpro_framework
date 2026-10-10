"""JSON-RPC 2.0 protocol: requests, batches and notifications over a resolved surface, and the
OpenRPC 1.4 document.

Context: a method is one `Resolved[RpcBinding]` of the gateway's surface (PRD_14), named by its
binding — `billing.issue_invoice`, never the layer (PRD_15 §4). What is published, and under
which name, is the gateway's and the wire's (`rpc.wire`); this module only speaks the protocol.
"""

import copy
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import ValidationError

from sincpro_framework.auth.domain.exceptions import AuthError
from sincpro_framework.auth.domain.identity import Credentials
from sincpro_framework.context.domain.level import EntrypointKind
from sincpro_framework.entrypoints.adapters.rpc.errors import (
    ERROR_DATA_SCHEMA,
    INVALID_PARAMS,
    PROTOCOL_ERRORS,
    Answer,
    answer_for,
    error_components,
    error_data,
    method_error_refs,
    protocol_error,
)
from sincpro_framework.entrypoints.domain.bindings import RpcBinding
from sincpro_framework.entrypoints.domain.layers import Scalar
from sincpro_framework.entrypoints.domain.surface import Resolved
from sincpro_framework.entrypoints.services.scalar_executor import execute
from sincpro_framework.exceptions import ClientError
from sincpro_framework.observability import process
from sincpro_framework.sincpro_logger import logger

DISCOVER_METHOD = "rpc.discover"
RESERVED_PREFIX = "rpc."
"""JSON-RPC 2.0 §4: method names beginning `rpc.` are the protocol's own — only discovery."""
OPENRPC_VERSION = "1.4.0"
DEFAULT_MAX_BATCH_SIZE = 50
DEFAULT_MAX_BODY_BYTES = 1024 * 1024

PARSE, INVALID, UNKNOWN_METHOD = PROTOCOL_ERRORS

type MethodIndex = Mapping[str, Resolved[RpcBinding]]
"""Each published method by its name — what a request's `method` is looked up in."""


class MethodNotFound(ClientError):
    def __init__(self, method: str):
        self.method = method
        super().__init__(method)


class InvalidParams(ClientError):
    """Params the method cannot take whatever their values — by position, on a by-name API."""

    def __init__(self, reason: str, message: str):
        self.reason = reason
        self.message = message
        super().__init__(message)


def jsonrpc_error(
    code: int, message: str, request_id: Any = None, data: Any = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def jsonrpc_answer(answer: Answer, request_id: Any = None) -> dict[str, Any]:
    code, message, data = answer
    return jsonrpc_error(code, message, request_id, data)


def parse_error(message: str) -> dict[str, Any]:
    return jsonrpc_answer(protocol_error(PARSE, "PARSE_ERROR", message=message))


def invalid_request(
    reason: str, message: str, request_id: Any = None, **disclosed: Any
) -> dict[str, Any]:
    answer = protocol_error(INVALID, reason, message=message, **disclosed)
    return jsonrpc_answer(answer, request_id)


def body_too_large(limit: int) -> dict[str, Any]:
    return invalid_request(
        "BODY_TOO_LARGE", f"a request body holds at most {limit} bytes", limit=limit
    )


def unsupported_media_type(accepted: list[str]) -> dict[str, Any]:
    return invalid_request(
        "UNSUPPORTED_MEDIA_TYPE", "send the request as application/json", accepted=accepted
    )


def jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def dispatch_method(
    methods: MethodIndex,
    discover: Callable[[], dict[str, Any]],
    method: str,
    params: Any,
    context: Mapping[str, Any] | None,
    credentials: Credentials | None = None,
) -> dict[str, Any]:
    """Execute one JSON-RPC method of the surface — through the bus, never `execute`.

    1. rpc.discover returns the OpenRPC document (no params).
    2. Unknown method → MethodNotFound.
    3. Params must be a by-name object (or omitted). An array is InvalidParams: every
       method is published `paramStructure: "by-name"`.
    4. Any exception propagates — a Pydantic ValidationError is answered invalid params.
    5. Final: the JSON object the Feature / ApplicationService returned.
    """
    if method == DISCOVER_METHOD:
        return discover()
    resolved = methods.get(method)
    if resolved is None:
        raise MethodNotFound(method)
    if params is None:
        payload: Scalar = {}
    elif isinstance(params, dict):
        payload = params
    else:
        raise InvalidParams(
            "PARAMS_BY_POSITION", "params must be a JSON object: this API is by-name only"
        )
    operation = resolved.operation
    return execute(
        operation.bus, operation.run, payload, context, credentials, EntrypointKind.RPC
    )


def is_valid_id(request_id: Any) -> bool:
    """JSON-RPC 2.0 §4: a String, a Number or Null — and a JSON `true` is not a Number."""
    if isinstance(request_id, bool):
        return False
    return request_id is None or isinstance(request_id, (str, int, float))


def invalid_request_of(request: Any) -> dict[str, Any] | None:
    """Why a request is not a valid Request object — answered even without an `id`: an
    invalid request is not a notification (JSON-RPC 2.0 §7, "invalid Request object")."""
    if not isinstance(request, dict):
        return invalid_request("NOT_AN_OBJECT", "a request must be a JSON object")
    request_id = request.get("id")
    if not is_valid_id(request_id):
        return invalid_request("INVALID_ID", "id must be a string, a number or null")
    if request.get("jsonrpc") != "2.0":
        return invalid_request("INVALID_VERSION", 'jsonrpc must be "2.0"', request_id)
    method = request.get("method")
    if not isinstance(method, str) or not method:
        return invalid_request("INVALID_METHOD", "method must be a string", request_id)
    params = request.get("params")
    if params is not None and not isinstance(params, (dict, list)):
        return invalid_request(
            "INVALID_PARAMS_STRUCTURE", "params must be an object or an array", request_id
        )
    context = request.get("context")
    if context is not None and not isinstance(context, dict):
        return invalid_request("INVALID_CONTEXT", "context must be an object", request_id)
    return None


def failure_of(method: str, error: Exception) -> Answer:
    """What a failed method is answered with — logged unless expected or already reported."""
    if isinstance(error, MethodNotFound):
        return protocol_error(UNKNOWN_METHOD, "METHOD_NOT_FOUND", method=method)
    if isinstance(error, InvalidParams):
        data = error_data("invalid", error.reason, False, message=error.message)
        return INVALID_PARAMS, "Invalid params", data
    expected = isinstance(error, (ValidationError, AuthError))
    if not expected and not process.was_reported(error):
        logger.exception("JSON-RPC method [%s] failed", method)
    return answer_for(error)


def handle_single(
    methods: MethodIndex,
    discover: Callable[[], dict[str, Any]],
    request: Any,
    inherited_context: Mapping[str, Any] | None = None,
    credentials: Credentials | None = None,
) -> dict[str, Any] | None:
    """Handle one JSON-RPC request object.

    1. An invalid Request object is answered, `id` or not.
    2. The body's `context` (a framework extension) is merged over the inherited one.
    3. Final: the answer — or None for a notification, which is never answered, even when it
       failed.
    """
    refused = invalid_request_of(request)
    if refused is not None:
        return refused
    is_notification = "id" not in request
    request_id = request.get("id")
    method = request["method"]
    merged: dict[str, Any] = dict(inherited_context or {})
    merged.update(request.get("context") or {})
    try:
        result = dispatch_method(
            methods, discover, method, request.get("params"), merged or None, credentials
        )
    except Exception as error:
        response = jsonrpc_answer(failure_of(method, error), request_id)
        return None if is_notification else response
    if is_notification:
        return None
    return jsonrpc_result(request_id, result)


def handle_payload(
    methods: MethodIndex,
    discover: Callable[[], dict[str, Any]],
    payload: Any,
    inherited_context: Mapping[str, Any] | None = None,
    credentials: Credentials | None = None,
    max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
) -> dict[str, Any] | list[Any] | None:
    """JSON-RPC 2.0 entry: one request, a batch, or a parse-level invalid payload.

    1. A list is a batch: empty, or longer than `max_batch_size`, is one Invalid Request and
       nothing runs. Each item is answered on its own; notifications are dropped from the
       reply.
    2. Anything else is a single request.
    3. Final: a response object, a list of responses, or None when nothing is answered.
    """
    if not isinstance(payload, list):
        return handle_single(methods, discover, payload, inherited_context, credentials)
    if not payload:
        return invalid_request("EMPTY_BATCH", "a batch must not be empty")
    if len(payload) > max_batch_size:
        return invalid_request(
            "BATCH_TOO_LARGE",
            f"a batch holds at most {max_batch_size} requests",
            limit=max_batch_size,
            size=len(payload),
        )
    replies = [
        handle_single(methods, discover, item, inherited_context, credentials)
        for item in payload
    ]
    visible = [item for item in replies if item is not None]
    return visible or None


def _rewritten_refs(node: Any, names: Mapping[str, str]) -> Any:
    if isinstance(node, dict):
        rewritten = {key: _rewritten_refs(value, names) for key, value in node.items()}
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            hoisted_name = names[ref.removeprefix("#/$defs/")]
            rewritten["$ref"] = f"#/components/schemas/{hoisted_name}"
        return rewritten
    if isinstance(node, list):
        return [_rewritten_refs(value, names) for value in node]
    return copy.deepcopy(node)


def hoisted(schema: dict[str, Any], schemas: dict[str, Any], owner: str) -> dict[str, Any]:
    """A DTO schema whose `$defs` live in the document's `components.schemas`.

    Context: Pydantic references a nested model as `#/$defs/Line`, relative to the DTO's own
    schema; inside an OpenRPC document that pointer resolves against the document, which has
    no `$defs`. Each definition moves to `components.schemas` — under its own name, or under
    `{owner}.{name}` when another DTO already published a different schema by that name. The
    catalog's schema is never mutated: it is computed once and shared by every wire.
    """
    definitions = schema.get("$defs") or {}
    same_names = {name: name for name in definitions}
    names = {
        name: (
            name
            if schemas.get(name) in (None, _rewritten_refs(definition, same_names))
            else f"{owner}.{name}"
        )
        for name, definition in definitions.items()
    }
    for name, definition in definitions.items():
        schemas[names[name]] = _rewritten_refs(definition, names)
    body = {key: value for key, value in schema.items() if key != "$defs"}
    return _rewritten_refs(body, names)


def content_descriptors(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn a DTO JSON Schema object into OpenRPC by-name params.

    1. Read properties and required from the DTO schema.
    2. Each field becomes a Content Descriptor (name, required, schema).
    3. Final: required params first (OpenRPC 1.4 asks it), each group in the order Pydantic
       emitted the properties.
    """
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    descriptors: list[dict[str, Any]] = []
    for name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            continue
        descriptors.append(
            {
                "name": name,
                "required": name in required,
                "schema": field_schema,
            }
        )
    return sorted(descriptors, key=lambda one: not one["required"])


def method_object(
    name: str, resolved: Resolved[RpcBinding], schemas: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One method of the document — no layer anywhere: a Feature promoted to an
    ApplicationService publishes the same object.

    1. Params and result from the DTOs' schemas, nested definitions hoisted to `schemas`.
    2. One tag, the context (the bus's alias) — the document is tagged per context.
    3. `x-sincpro-requires`: what the use case declared (`public`, `authenticated`, the
       requirements, `unguarded`); `x-idempotent`: a Query, or `@idempotency.once` — a retry
       is safe.
    4. Final: `deprecated` and `x-sunset` when its binding is on its way out.

    Context: `schemas` is the document's `components.schemas`; without one the hoisted
    definitions go to a dict nobody keeps.
    """
    operation = resolved.operation
    published: dict[str, Any] = schemas if schemas is not None else {}
    params = hoisted(operation.json_schema, published, name)
    result = hoisted(operation.response_json_schema or {"type": "object"}, published, name)
    method: dict[str, Any] = {
        "name": name,
        "summary": operation.description.split("\n", 1)[0],
        "description": operation.description,
        "tags": [{"name": operation.alias}],
        "paramStructure": "by-name",
        "params": content_descriptors(params),
        "result": {"name": "result", "schema": result},
        "errors": method_error_refs(),
        "x-sincpro-context": operation.alias,
        "x-sincpro-dto": operation.command.__name__,
        "x-idempotent": operation.idempotent or operation.is_query,
    }
    if operation.access is not None:
        method["x-sincpro-requires"] = operation.access
    deprecation = resolved.binding.deprecated
    if deprecation is not None:
        method["deprecated"] = True
        if deprecation.sunset is not None:
            method["x-sunset"] = deprecation.sunset.isoformat()
    return method


def discover_method_object() -> dict[str, Any]:
    return {
        "name": DISCOVER_METHOD,
        "summary": "OpenRPC service discovery",
        "description": "Return this server's OpenRPC document.",
        "paramStructure": "by-name",
        "params": [],
        "result": {
            "name": "OpenRPC Document",
            "schema": {"type": "object"},
        },
    }


CONTEXT_EXTENSION = {
    "description": "Not part of JSON-RPC 2.0 — a framework extension. A request object may "
    "carry a `context` member beside `params`; its keys are opened as the use case's "
    "`self.context` (`trace_id`, `span_id` and `carrier` as its trace). The client writes it: "
    "who is calling is never read from it. A server that is not this framework ignores it.",
    "schema": {"type": "object"},
}


def openrpc_document(
    title: str,
    methods: list[dict[str, Any]],
    version: str = "1.0.0",
    schemas: Mapping[str, Any] | None = None,
    max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> dict[str, Any]:
    return {
        "openrpc": OPENRPC_VERSION,
        "info": {"title": title, "version": version},
        "methods": [discover_method_object(), *methods],
        "components": {
            "errors": error_components(),
            "schemas": {**(schemas or {}), "ErrorData": dict(ERROR_DATA_SCHEMA)},
        },
        "x-sincpro-context": CONTEXT_EXTENSION,
        "x-sincpro-limits": {"maxBatchSize": max_batch_size, "maxBodyBytes": max_body_bytes},
    }
