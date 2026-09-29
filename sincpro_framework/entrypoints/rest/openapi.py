"""The OpenAPI 3.1 document of a REST gateway — written from the catalog, as the OpenRPC document
and the `.proto` files are, so every wire describes the same use cases the same way.

Context: each DTO's own schema carries its nested models under `$defs`; here they are lifted to
`components/schemas` and every reference points there, so a generator (orval, openapi-typescript)
names each model once. Security comes from the guarded buses' providers (`security_scheme()`),
and each operation says what it requires as `x-sincpro-requires` — the declarations
`AccessControl` holds, readable by a person, a gateway or an agent.
"""

import copy
from collections.abc import Iterable, Mapping
from typing import Any

from sincpro_framework.auth.access_control import access_control_of
from sincpro_framework.entrypoints.rest.routing import RestRoute, is_plain
from sincpro_framework.sincpro_logger import logger

OPENAPI_VERSION = "3.1.0"

REFUSAL = {
    "type": "object",
    "properties": {
        "kind": {"type": "string"},
        "reason": {"type": "string"},
        "message": {"type": "string"},
        "requirement": {"type": "string"},
        "step_up": {"type": "string"},
        "detail": {},
    },
    "required": ["kind"],
}

FAILURES = {
    "400": "The request could not be read",
    "401": "Who is calling is not known — see WWW-Authenticate",
    "403": "The identity may not do this",
    "409": "The write collided: a newer version, or a duplicate",
    "422": "Validation refused the DTO, or the domain refused the request",
}


class _Components:
    """The models every schema references, each under one name."""

    def __init__(self) -> None:
        self.schemas: dict[str, Any] = {
            "Error": {"type": "object", "properties": {"error": REFUSAL}}
        }

    def _pointed(self, node: Any) -> Any:
        if isinstance(node, dict):
            return {
                key: (
                    value.replace("#/$defs/", "#/components/schemas/")
                    if key == "$ref" and isinstance(value, str)
                    else self._pointed(value)
                )
                for key, value in node.items()
            }
        if isinstance(node, list):
            return [self._pointed(one) for one in node]
        return node

    def keep(self, name: str, schema: Mapping[str, Any]) -> dict[str, Any]:
        """`schema` kept as `name`, its `$defs` lifted beside it; a reference to it answered.
        Context: two models under one name with different shapes keep the first, and say so.
        """
        owned = copy.deepcopy(dict(schema))
        for nested, definition in owned.pop("$defs", {}).items():
            self._put(nested, self._pointed(definition))
        self._put(name, self._pointed(owned))
        return {"$ref": f"#/components/schemas/{name}"}

    def _put(self, name: str, schema: dict[str, Any]) -> None:
        held = self.schemas.get(name)
        if held is not None and held != schema:
            logger.warning(
                f"OpenAPI: two models are named {name}; the first one is published"
            )
            return
        self.schemas[name] = schema


def operation_security(bus: Any, dto: type) -> dict[str, Any]:
    """`security` and `x-sincpro-requires` of the operation answering `dto` on `bus`, when the
    bus is guarded — the fragment every OpenAPI host of the REST wire shares."""
    access = access_control_of(bus)
    if access is None:
        return {}
    declaration = access.requirements_of(dto)
    if declaration is None or declaration.kind == "public":
        return {"security": []} if declaration is not None else {}
    schemes = [one.name for one in access.providers if one.security_scheme() is not None]
    described: dict[str, Any] = {"security": [{name: []} for name in schemes]}
    if declaration.kind == "requires":
        described["x-sincpro-requires"] = [str(one) for one in declaration.requirements]
        described["x-sincpro-when-denied"] = declaration.when_denied.value
    return described


def security_schemes(buses: Iterable[Any]) -> dict[str, Any]:
    """Every provider of the guarded `buses` as an OpenAPI security scheme, once each."""
    schemes: dict[str, Any] = {}
    for bus in buses:
        access = access_control_of(bus)
        for provider in access.providers if access else ():
            scheme = provider.security_scheme()
            if scheme is not None:
                schemes.setdefault(provider.name, scheme)
    return schemes


def _security(route: RestRoute) -> dict[str, Any]:
    return operation_security(route.bus, route.operation.dto)


def _query_parameters(
    schema: Mapping[str, Any], components: _Components
) -> list[dict[str, Any]]:
    required = set(schema.get("required") or ())
    parameters: list[dict[str, Any]] = []
    for name, field_schema in (schema.get("properties") or {}).items():
        pointed = components._pointed(field_schema)
        parameter: dict[str, Any] = {
            "name": name,
            "in": "query",
            "required": name in required,
        }
        if is_plain(field_schema):
            parameter["schema"] = pointed
        else:
            parameter["content"] = {"application/json": {"schema": pointed}}
        parameters.append(parameter)
    return parameters


def _operation(route: RestRoute, method: str, components: _Components) -> dict[str, Any]:
    operation = route.operation
    input_ref = components.keep(operation.name, operation.json_schema)
    lifted = components.schemas[operation.name]
    answered = (
        components.keep(
            operation.response_json_schema.get("title", f"{operation.name}Answer"),
            operation.response_json_schema,
        )
        if operation.response_json_schema
        else {"type": "object"}
    )
    described: dict[str, Any] = {
        "operationId": (
            f"{route.operation_id}_by_body"
            if method == "POST" and "GET" in route.methods
            else route.operation_id
        ),
        "summary": operation.description.split("\n", 1)[0],
        "description": operation.description,
        "tags": [route.alias, operation.layer],
        "responses": {
            "200": {
                "description": "Answered",
                "content": {"application/json": {"schema": answered}},
            },
            **{
                status: {
                    "description": text,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
                    },
                }
                for status, text in FAILURES.items()
            },
        },
        "x-sincpro-dto": operation.name,
        "x-sincpro-layer": operation.layer,
        **_security(route),
    }
    parameters = [
        {
            "name": name,
            "in": "path",
            "required": True,
            "schema": (lifted.get("properties") or {}).get(name, {"type": "string"}),
        }
        for name in route.path_parameters
    ]
    if method == "GET":
        remaining = {
            **lifted,
            "properties": {
                name: value
                for name, value in (lifted.get("properties") or {}).items()
                if name not in route.path_parameters
            },
        }
        parameters += _query_parameters(remaining, components)
    else:
        described["requestBody"] = {
            "required": False,
            "content": {"application/json": {"schema": input_ref}},
        }
    if parameters:
        described["parameters"] = parameters
    return described


def openapi_document(
    title: str,
    version: str,
    routes: Iterable[RestRoute],
    servers: Iterable[str] = (),
) -> dict[str, Any]:
    """1. One path item per route, one operation per method it answers.
    2. The providers of every guarded bus as `securitySchemes`, once each.
    3. Final: the document, its models under `components/schemas`.
    """
    components = _Components()
    paths: dict[str, dict[str, Any]] = {}
    schemes: dict[str, Any] = {}
    listed = list(routes)
    for route in listed:
        item = paths.setdefault(route.path, {})
        for method in route.methods:
            item[method.lower()] = _operation(route, method, components)
        access = access_control_of(route.bus)
        for provider in access.providers if access else ():
            scheme = provider.security_scheme()
            if scheme is not None:
                schemes.setdefault(provider.name, scheme)
    document: dict[str, Any] = {
        "openapi": OPENAPI_VERSION,
        "info": {"title": title, "version": version},
        "paths": paths,
        "components": {"schemas": components.schemas},
    }
    if schemes:
        document["components"]["securitySchemes"] = schemes
    if servers:
        document["servers"] = [{"url": url} for url in servers]
    return document
