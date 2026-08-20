"""JSON-RPC 2.0 wire: dispatch over the shared catalog, and the OpenRPC 1.4 document."""

import json
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import ValidationError

from sincpro_framework.entrypoints.catalog import PackedFeatureOrAppService
from sincpro_framework.entrypoints.const import Scalar
from sincpro_framework.entrypoints.scalar_executor import execute
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
DISCOVER_METHOD = "rpc.discover"
OPENRPC_VERSION = "1.4.0"

MethodIndex = dict[str, tuple[str, UseFramework, PackedFeatureOrAppService]]


class MethodNotFound(Exception):
    def __init__(self, method: str):
        self.method = method
        super().__init__(method)


class InvalidParams(Exception):
    def __init__(self, data: Any):
        self.data = data
        super().__init__(str(data))


def method_name(instance: str, layer: str, dto_name: str) -> str:
    return f"{instance}.{layer}.{dto_name}"


def json_safe_validation_errors(error: ValidationError) -> list[dict[str, Any]]:
    """Pydantic puts the raw exception in ctx.error for value_error types (any
    ValueObject validate_fn that rejects input). json.dumps on that raises, so the
    JSON-RPC error envelope would crash the host instead of returning -32602.
    """
    return json.loads(json.dumps(error.errors(), default=str))


def jsonrpc_error(
    code: int, message: str, request_id: Any = None, data: Any = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def dispatch_method(
    methods: MethodIndex,
    discover: Callable[[], dict[str, Any]],
    method: str,
    params: Any,
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Execute one JSON-RPC method against the catalog.

    1. rpc.discover returns the OpenRPC document (no params).
    2. Unknown method → MethodNotFound.
    3. Params must be a by-name object (or omitted). Arrays are invalid.
    4. Pydantic ValidationError → InvalidParams. Any other exception propagates.
    5. Final: the JSON object the Feature / ApplicationService returned.
    """
    if method == DISCOVER_METHOD:
        return discover()
    bound = methods.get(method)
    if bound is None:
        raise MethodNotFound(method)
    if params is None:
        payload: Scalar = {}
    elif isinstance(params, dict):
        payload = params
    else:
        raise InvalidParams("params must be a JSON object (by-name)")
    _alias, framework, operation = bound
    try:
        return execute(framework, operation.run, payload, context)
    except ValidationError as error:
        raise InvalidParams(json_safe_validation_errors(error)) from error


def handle_single(
    methods: MethodIndex,
    discover: Callable[[], dict[str, Any]],
    request: Any,
    inherited_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Handle one JSON-RPC request object. Notifications (no id) return None."""
    if not isinstance(request, dict):
        return jsonrpc_error(INVALID_REQUEST, "Request must be an object")
    if request.get("jsonrpc") != "2.0":
        return jsonrpc_error(INVALID_REQUEST, 'jsonrpc must be "2.0"', request.get("id"))
    method = request.get("method")
    if not isinstance(method, str) or not method:
        return jsonrpc_error(INVALID_REQUEST, "method must be a string", request.get("id"))
    is_notification = "id" not in request
    request_id = request.get("id") if not is_notification else None
    extra_context = request.get("context")
    if extra_context is not None and not isinstance(extra_context, dict):
        response = jsonrpc_error(INVALID_REQUEST, "context must be an object", request_id)
        return None if is_notification else response
    merged: dict[str, Any] = dict(inherited_context or {})
    if extra_context:
        merged.update(extra_context)
    try:
        result = dispatch_method(
            methods, discover, method, request.get("params"), merged or None
        )
    except MethodNotFound:
        response = jsonrpc_error(METHOD_NOT_FOUND, "Method not found", request_id, method)
        return None if is_notification else response
    except InvalidParams as error:
        response = jsonrpc_error(INVALID_PARAMS, "Invalid params", request_id, error.data)
        return None if is_notification else response
    except Exception as error:
        logger.exception("JSON-RPC method [%s] failed", method)
        response = jsonrpc_error(INTERNAL_ERROR, "Internal error", request_id, str(error))
        return None if is_notification else response
    if is_notification:
        return None
    return jsonrpc_result(request_id, result)


def handle_payload(
    methods: MethodIndex,
    discover: Callable[[], dict[str, Any]],
    payload: Any,
    inherited_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | list[Any] | None:
    """JSON-RPC 2.0 entry: one request, a batch, or a parse-level invalid payload.

    1. A list is a batch; empty list is invalid. Notifications are dropped from the reply.
    2. An object is a single request.
    3. Anything else is invalid request.
    4. Final: a response object, a list of responses, or None when every item was a notification.
    """
    if isinstance(payload, list):
        if not payload:
            return jsonrpc_error(INVALID_REQUEST, "Batch must not be empty")
        replies = [
            handle_single(methods, discover, item, inherited_context) for item in payload
        ]
        visible = [item for item in replies if item is not None]
        return visible or None
    return handle_single(methods, discover, payload, inherited_context)


def content_descriptors(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn a DTO JSON Schema object into OpenRPC by-name params.

    1. Read properties and required from the DTO schema.
    2. Each field becomes a Content Descriptor (name, required, schema).
    3. Final: descriptors in the same order Pydantic emitted the properties.
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
    return descriptors


def method_object(
    name: str, operation: PackedFeatureOrAppService, instance: str
) -> dict[str, Any]:
    return {
        "name": name,
        "summary": operation.description.split("\n", 1)[0],
        "description": operation.description,
        "tags": [{"name": instance}, {"name": operation.layer}],
        "paramStructure": "by-name",
        "params": content_descriptors(operation.json_schema),
        "result": {
            "name": "result",
            "schema": {"type": "object"},
        },
        "x-sincpro-instance": instance,
        "x-sincpro-layer": operation.layer,
        "x-sincpro-dto": operation.name,
    }


def discover_method_object() -> dict[str, Any]:
    return {
        "name": DISCOVER_METHOD,
        "summary": "OpenRPC service discovery",
        "description": "Return this server's OpenRPC document.",
        "params": [],
        "result": {
            "name": "OpenRPC Document",
            "schema": {"type": "object"},
        },
    }


def openrpc_document(
    title: str,
    methods: list[dict[str, Any]],
    version: str = "1.0.0",
) -> dict[str, Any]:
    return {
        "openrpc": OPENRPC_VERSION,
        "info": {"title": title, "version": version},
        "methods": [discover_method_object(), *methods],
    }
