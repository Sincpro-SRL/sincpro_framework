"""The REST wire, without its web library: which route answers each use case, how a request
becomes its payload, and what HTTP answers each failure.

    POST /billing/command-issue-invoice              a Command — its body is the DTO
    GET  /billing/query-invoices?criteria=<json>     a `Query` — its fields in the query string,
    POST /billing/query-invoices                     or in a body when they do not fit a URL

Context: the path is the bus's alias and the DTO's name in kebab case — never the layer, so a
Feature that becomes an ApplicationService is still served at the same URL. A field that is not a plain value — a
`Criteria`, a list, a nested object — travels in the query string as JSON, the form
`@sincpro/criteria`'s `pack()` writes. A route of the project's own (`GET
/billing/invoices/{invoice_id}`) takes the path's fields from the path.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from sincpro_framework.auth import Credentials
from sincpro_framework.common.failures import (
    json_safe_validation_errors,
    refined_failure_kind,
)
from sincpro_framework.common.naming import kebab
from sincpro_framework.context import EntrypointKind
from sincpro_framework.ddd.query import Query
from sincpro_framework.entrypoints.adapters.rest import exceptions
from sincpro_framework.entrypoints.domain.layers import Scalar
from sincpro_framework.entrypoints.domain.packed import PackedFeatureOrAppService
from sincpro_framework.observability import process
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework

PATH_PARAMETER = re.compile(r"{([A-Za-z_][A-Za-z0-9_]*)}")
PLAIN_TYPES = {"string", "integer", "number", "boolean"}


def is_query(operation: PackedFeatureOrAppService) -> bool:
    """A use case that reads: its DTO is a `Query` — answered on GET, cacheable."""
    return isinstance(operation.dto, type) and issubclass(operation.dto, Query)


@dataclass(frozen=True)
class RestRoute:
    """One use case on the REST wire."""

    alias: str
    bus: UseFramework
    operation: PackedFeatureOrAppService
    path: str
    methods: tuple[str, ...]
    operation_id: str
    path_parameters: tuple[str, ...] = field(default=())

    @property
    def dto_name(self) -> str:
        """The name of the DTO this route answers — `CommandIssueInvoice`."""
        return self.operation.name


def parse_route(spec: str) -> tuple[tuple[str, ...], str]:
    """`"GET /billing/invoices/{invoice_id}"` → `(("GET",), "/billing/invoices/{invoice_id}")`;
    several methods as `"GET,POST /…"`."""
    method, _, path = spec.strip().partition(" ")
    if not path.startswith("/"):
        raise ValueError(f"a route is 'METHOD /path', got {spec!r}")
    return tuple(one.strip().upper() for one in method.split(",")), path.strip()


def build_routes(
    operations: Iterable[tuple[str, UseFramework, PackedFeatureOrAppService]],
    prefix: str,
    overrides: Mapping[str, str],
) -> list[RestRoute]:
    """Every use case's route. A DTO named on two buses is published under both, its
    `operationId` qualified by the alias so a generated client tells them apart.

        build_routes([("billing", billing, <CommandIssueInvoice>),
                      ("billing", billing, <QueryInvoice>)], "/api", {})
        ->  [RestRoute(path="/api/billing/command-issue-invoice", methods=("POST",)),
             RestRoute(path="/api/billing/query-invoice", methods=("GET", "POST"))]
        build_routes([...], "", {"QueryInvoice": "GET /billing/invoices/{invoice_id}"})
        ->  QueryInvoice on GET /billing/invoices/{invoice_id}, path_parameters
            ("invoice_id",)

    1. The route the project gave the DTO, or `{prefix}/{alias}/{kebab(name)}` — GET and POST for
       a `Query`, POST for anything else.
    2. Final: refused when two use cases would answer the same method on the same path.
    """
    listed = list(operations)
    repeated = {
        name
        for name in (operation.name for _, _, operation in listed)
        if sum(1 for _, _, one in listed if one.name == name) > 1
    }
    routes: list[RestRoute] = []
    taken: dict[tuple[str, str], str] = {}
    for alias, bus, operation in listed:
        spec = overrides.get(operation.name)
        if spec is not None:
            methods, path = parse_route(spec)
            path = f"{prefix}{path}"
        else:
            methods = ("GET", "POST") if is_query(operation) else ("POST",)
            path = f"{prefix}/{alias}/{kebab(operation.name)}"
        for method in methods:
            clash = taken.get((method, path))
            if clash is not None:
                raise ValueError(f"{method} {path} answers both {clash} and {operation.name}")
            taken[(method, path)] = operation.name
        operation_id = (
            f"{alias}_{operation.name}" if operation.name in repeated else operation.name
        )
        routes.append(
            RestRoute(
                alias=alias,
                bus=bus,
                operation=operation,
                path=path,
                methods=methods,
                operation_id=operation_id,
                path_parameters=tuple(PATH_PARAMETER.findall(path)),
            )
        )
    return routes


def is_plain(schema: Mapping[str, Any]) -> bool:
    """A field the query string carries as itself: a string, a number, a boolean, an enum of
    them, or an optional one. Anything else travels as JSON.

        is_plain({"type": "integer"})                                     ->  True
        is_plain({"anyOf": [{"type": "string"}, {"type": "null"}]})       ->  True
        is_plain({"type": "array", "items": {"type": "string"}})          ->  False

    A union is plain only when every member is — `null` aside.
    """
    if schema.get("type") in PLAIN_TYPES or "enum" in schema or "const" in schema:
        return True
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        return all(one.get("type") == "null" or is_plain(one) for one in options)
    return False


def payload_from_query(query: Mapping[str, str], schema: Mapping[str, Any]) -> Scalar:
    """The DTO's values out of a query string — each plain field as it came, pydantic coercing
    it; any other decoded from JSON. A name the DTO does not have is left for validation to
    refuse.

        payload_from_query({"status": "open", "tags": '["a","b"]'}, <schema>)
        ->  {"status": "open", "tags": ["a", "b"]}
        payload_from_query({"tags": "a,b"}, <schema>)
        ->  InvalidRequest: tags is not JSON: Expecting value

    1. The schema's properties.
    2. For each query value:
       2.1 a name the DTO does not have, or a plain field (`is_plain`): passed as it came;
       2.2 any other: decoded from JSON — refused as `InvalidRequest` (400) when it is not.
    Final: the payload, still to be validated as the DTO.
    """
    properties = schema.get("properties") or {}
    payload: dict[str, Any] = {}
    for name, value in query.items():
        field_schema = properties.get(name)
        if field_schema is None or is_plain(field_schema):
            payload[name] = value
            continue
        try:
            payload[name] = json.loads(value)
        except json.JSONDecodeError as error:
            raise exceptions.InvalidRequest(f"{name} is not JSON: {error.msg}") from error
    return payload


STATUS_OF = {
    exceptions.FailureKind.INVALID: 422,
    exceptions.FailureKind.UNAUTHENTICATED: 401,
    exceptions.FailureKind.PERMISSION_DENIED: 403,
    exceptions.FailureKind.NOT_FOUND: 404,
    exceptions.FailureKind.CONFLICT: 409,
    exceptions.FailureKind.IN_PROGRESS: 409,
    exceptions.FailureKind.KEY_REUSED: 422,
    exceptions.FailureKind.DOMAIN: 422,
    exceptions.FailureKind.EXHAUSTED: 429,
    exceptions.FailureKind.UNAVAILABLE: 503,
    exceptions.FailureKind.UNKNOWN_OUTCOME: 504,
    exceptions.FailureKind.INTERNAL: 500,
}


def failure_answer(error: Exception) -> tuple[int, dict[str, Any]]:
    """The status and body a failure is answered with — its kind (`common.failures`, the same
    on every wire), and the reason for what the caller may read, never the inside of the
    process. A request that could not be read at all is a 400.

        failure_answer(InvalidRequest("the body is not JSON"))
        ->  (400, {"kind": "invalid", "message": "the body is not JSON"})
        failure_answer(RuntimeError("db password=..."))
        ->  (500, {"kind": "internal", "message": "Internal error"})

    1. What could not be read: 400.
    2. Anything else classified once (`refined_failure_kind`), its status by kind.
    3. Its body: the validation errors, the auth refusal (`AuthError.refusal_body`), or the
       message.
    Final: an internal failure says nothing of the inside.
    """
    if isinstance(error, exceptions.InvalidRequest):
        return 400, {"kind": exceptions.FailureKind.INVALID.value, "message": str(error)}
    kind = refined_failure_kind(error)
    if isinstance(error, ValidationError):
        return STATUS_OF[kind], {
            "kind": kind.value,
            "detail": json_safe_validation_errors(error),
        }
    if isinstance(error, exceptions.AuthError):
        return STATUS_OF[kind], error.refusal_body()
    if kind == exceptions.FailureKind.INTERNAL:
        return STATUS_OF[kind], {"kind": kind.value, "message": "Internal error"}
    return STATUS_OF[kind], {"kind": kind.value, "message": str(error)}


def call_route(
    route: RestRoute,
    payload: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    credentials: Credentials | None = None,
) -> tuple[int, Any]:
    """One call of `route`, answered as `(status, body)` — what a web library turns into a
    response; the transport-free path a test or another host takes.

        call_route(<POST /billing/command-issue-invoice>, {"total": 10})
        ->  (200, {"invoice_id": "F-1", "total": 10})
        call_route(<same route>, {"total": "ten"})
        ->  (422, {"error": {"kind": "invalid", "detail": [...]}})

    1. The use case's `call`, inside the context and as whoever the credentials say — a REST
       entrypoint.
    2. A failure answered by its kind (`failure_answer`) under `error`.
    Final: a 500 is logged here unless a bus already reported it; the body never says why.
    """
    try:
        return 200, route.operation.call(
            dict(payload), context, credentials, EntrypointKind.REST
        )
    except Exception as error:
        status, body = failure_answer(error)
        if status == 500 and not process.was_reported(error):
            logger.exception("REST %s failed", route.path)
        return status, {"error": body}
