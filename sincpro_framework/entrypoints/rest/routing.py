"""The REST wire, without its web library: which route answers each use case, how a request
becomes its payload, and what HTTP answers each failure.

    POST /billing/command-issue-invoice              a Command — its body is the DTO
    GET  /billing/query-invoices?criteria=<json>     a `Query` — its fields in the query string,
    POST /billing/query-invoices                     or in a body when they do not fit a URL

Context: the path is the bus's alias and the DTO's name in kebab case — never the layer, so a
Feature that becomes an ApplicationService keeps its URL. A field that is not a plain value — a
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

from sincpro_framework.auth.domain import AuthError
from sincpro_framework.auth.transports import refusal_body
from sincpro_framework.ddd.query import Query
from sincpro_framework.entrypoints.catalog import PackedFeatureOrAppService
from sincpro_framework.entrypoints.const import Scalar
from sincpro_framework.entrypoints.errors import (
    FailureKind,
    failure_kind,
    json_safe_validation_errors,
)
from sincpro_framework.use_bus import UseFramework

PATH_PARAMETER = re.compile(r"{([A-Za-z_][A-Za-z0-9_]*)}")
PLAIN_TYPES = {"string", "integer", "number", "boolean"}


def kebab(name: str) -> str:
    """`CommandIssueInvoice` → `command-issue-invoice`; `HTTPRequest` → `http-request`."""
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", spaced).lower()


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
    them, or an optional one. Anything else travels as JSON."""
    if schema.get("type") in PLAIN_TYPES or "enum" in schema or "const" in schema:
        return True
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        return all(one.get("type") == "null" or is_plain(one) for one in options)
    return False


def payload_from_query(query: Mapping[str, str], schema: Mapping[str, Any]) -> Scalar:
    """The DTO's values out of a query string — each plain field as it came, pydantic coercing
    it; any other decoded from JSON. A name the DTO does not have is left for validation to
    refuse."""
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
            raise InvalidRequest(f"{name} is not JSON: {error.msg}") from error
    return payload


class InvalidRequest(Exception):
    """A request the wire cannot read into a payload — answered 400."""


STATUS_OF = {
    FailureKind.INVALID: 422,
    FailureKind.UNAUTHENTICATED: 401,
    FailureKind.PERMISSION_DENIED: 403,
    FailureKind.CONFLICT: 409,
    FailureKind.DOMAIN: 422,
    FailureKind.INTERNAL: 500,
}


def failure_answer(error: Exception) -> tuple[int, dict[str, Any]]:
    """The status and body a failure is answered with — its kind (`entrypoints.errors`, the same
    on every wire), and the reason for what the caller may read, never the inside of the
    process. A request that could not be read at all is a 400."""
    if isinstance(error, InvalidRequest):
        return 400, {"kind": FailureKind.INVALID.value, "message": str(error)}
    kind = failure_kind(error)
    if isinstance(error, ValidationError):
        return STATUS_OF[kind], {
            "kind": kind.value,
            "detail": json_safe_validation_errors(error),
        }
    if isinstance(error, AuthError):
        return STATUS_OF[kind], refusal_body(error)
    if kind == FailureKind.INTERNAL:
        return STATUS_OF[kind], {"kind": kind.value, "message": "Internal error"}
    return STATUS_OF[kind], {"kind": kind.value, "message": str(error)}
