"""The served methods as a `Describe` document and `.proto` source — `naming.py` decides the
names.

Rendering only — no `grpc` or `protobuf` import, so a build step can export the `.proto` for the
Go/TypeScript/Java clients without installing the `[grpc]` extra.

Payloads are `google.protobuf.Struct` on every method, not a generated message
per DTO. A typed message would need **stable field numbers**, and a Pydantic DTO
has no such registry: inserting a field in the middle of the class would renumber
everything after it and silently break every deployed client. `Struct` keys are
names, which is the same compatibility contract the JSON-RPC wire already has.
The field-level types still travel — as the JSON Schema in `Describe`.
"""

import re
from collections.abc import Mapping
from typing import Any

from sincpro_framework.entrypoints.const import Scalar
from sincpro_framework.entrypoints.exposure import Operation
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.use_bus import UseFramework

PACKAGE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
STRUCT_TYPE = "google.protobuf.Struct"
STRUCT_IMPORT = "google/protobuf/struct.proto"
INTROSPECTION_PACKAGE = "sincpro"
INTROSPECTION_SERVICE = "Introspection"
DESCRIBE_METHOD = "Describe"
DESCRIBE_SERVICE = f"{INTROSPECTION_PACKAGE}.{INTROSPECTION_SERVICE}"
DESCRIBE_PATH = f"/{DESCRIBE_SERVICE}/{DESCRIBE_METHOD}"
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
"""grpcio's own receive limit, made explicit and applied both ways — `max_message_bytes=`."""
MAX_CONNECTION_AGE = 300.0
"""Seconds a connection lives before the server asks the client to reconnect — so a rolling
deploy or a new replica is rebalanced onto, instead of every client pinned to the old pods."""
RESERVED_SERVICES = frozenset(
    {
        DESCRIBE_SERVICE,
        "sincpro.Contexts",
        "grpc.health.v1.Health",
        "grpc.reflection.v1.ServerReflection",
        "grpc.reflection.v1alpha.ServerReflection",
    }
)
"""What every server of the framework answers by itself — no published method may take it."""


class GrpcMethodSpec(DataTransferObject):
    """One published use case as one unary gRPC method.

    `path` is what a client dials (`/billing.v1.BillingService/IssueInvoice`); `service` the
    fully qualified service, inside `package`; `operation` the resolved facts, with the bound
    `run` every call goes through.
    """

    alias: str
    package: str
    service: str
    method: str
    path: str
    framework: UseFramework
    operation: Operation
    deprecated: bool = False


def validate_package(package: str) -> str:
    """A gRPC package is dotted proto identifiers: no hyphen, unlike a JSON-RPC alias."""
    if not PACKAGE_PATTERN.match(package):
        raise ValueError(
            f"gRPC package [{package}] must match {PACKAGE_PATTERN.pattern} "
            "— a proto package allows no hyphen"
        )
    return package


def group_by_service(
    specs: Mapping[str, GrpcMethodSpec],
) -> dict[str, list[GrpcMethodSpec]]:
    """Specs grouped by fully qualified service name, insertion order preserved."""
    grouped: dict[str, list[GrpcMethodSpec]] = {}
    for spec in specs.values():
        grouped.setdefault(spec.service, []).append(spec)
    return grouped


def group_by_package(
    specs: Mapping[str, GrpcMethodSpec],
) -> dict[str, dict[str, list[GrpcMethodSpec]]]:
    """`{package: {service short name: specs}}` — one `.proto` file, one descriptor per package."""
    grouped: dict[str, dict[str, list[GrpcMethodSpec]]] = {}
    for service, entries in group_by_service(specs).items():
        package = entries[0].package
        grouped.setdefault(package, {})[service[len(package) + 1 :]] = entries
    return grouped


def file_name(package: str) -> str:
    """Buf's layout: `billing.v1` → `billing/v1/billing.proto`."""
    parts = package.split(".")
    named = [one for one in parts if not re.fullmatch(r"v\d+", one)] or parts
    return f"{'/'.join(parts)}/{named[-1]}.proto"


def describe_document(
    title: str, version: str, specs: Mapping[str, GrpcMethodSpec]
) -> Scalar:
    """The catalog a client reads from `sincpro.Introspection/Describe`.

    Reflection answers *which methods exist*; this answers *what they take and
    what they answer*. `params` is the input DTO's JSON Schema — the same one MCP
    publishes as a tool schema and OpenRPC as content descriptors — and `result`
    is the declared response's, an open object when `execute` declares none.
    """
    services: list[dict[str, Any]] = []
    for service, entries in group_by_service(specs).items():
        services.append(
            {
                "name": service,
                "package": entries[0].package,
                "alias": entries[0].alias,
                "methods": [
                    {
                        "name": spec.method,
                        "path": spec.path,
                        "command": spec.operation.command.__name__,
                        "kind": str(spec.operation.layer),
                        "deprecated": spec.deprecated,
                        "description": spec.operation.description,
                        "params": spec.operation.json_schema,
                        "result": spec.operation.response_json_schema or {"type": "object"},
                    }
                    for spec in entries
                ],
            }
        )
    return {"title": title, "version": version, "services": services}


def _comment(text: str, indent: str) -> list[str]:
    return [f"{indent}// {line}".rstrip() for line in text.splitlines()]


def _result_hint(spec: GrpcMethodSpec) -> str:
    """The response shape as a comment, since the message is always a Struct.

    A generated stub cannot carry it, so the `.proto` says what the Struct holds
    and `Describe` carries the same shape as a full JSON Schema.
    """
    schema = spec.operation.response_json_schema
    if not schema:
        return "Struct: response shape not declared by execute()."
    title = schema.get("title")
    if title:
        return f"Struct: the fields of {title}. Full schema in Describe."
    return f"Struct: {schema.get('type', 'object')}. Full schema in Describe."


def _service_block(name: str, entries: list[GrpcMethodSpec]) -> list[str]:
    lines = [f"service {name} {{"]
    for spec in entries:
        lines.extend(_comment(spec.operation.description, "  "))
        lines.extend(_comment(_result_hint(spec), "  "))
        signature = f"  rpc {spec.method}({STRUCT_TYPE}) returns ({STRUCT_TYPE})"
        lines.append(
            f"{signature} {{ option deprecated = true; }}"
            if spec.deprecated
            else f"{signature};"
        )
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    lines.append("}")
    return lines


def _file(package: str, blocks: list[list[str]]) -> str:
    header = [
        "// Generated by sincpro-framework from the bus catalog. Do not edit by hand.",
        'syntax = "proto3";',
        "",
        f"package {package};",
        "",
        f'import "{STRUCT_IMPORT}";',
        "",
    ]
    body: list[str] = []
    for block in blocks:
        body.extend(block)
        body.append("")
    return "\n".join([*header, *body]).rstrip() + "\n"


def introspection_proto() -> str:
    """The fixed service every sincpro gRPC gateway serves."""
    return _file(
        INTROSPECTION_PACKAGE,
        [
            [
                "// The bus catalog: every method, its layer and its input JSON Schema.",
                f"service {INTROSPECTION_SERVICE} {{",
                f"  rpc {DESCRIBE_METHOD}({STRUCT_TYPE}) returns ({STRUCT_TYPE});",
                "}",
            ]
        ],
    )


def proto_files(specs: Mapping[str, GrpcMethodSpec]) -> dict[str, str]:
    """`{path: source}` — one file per package (`billing/v1/billing.proto`), plus
    `sincpro.proto`.

    One package per file, because proto allows exactly one. The client team generates stubs
    from these; the server serves precisely what they describe.
    """
    files = {
        file_name(package): _file(
            package, [_service_block(name, entries) for name, entries in services.items()]
        )
        for package, services in group_by_package(specs).items()
    }
    files[f"{INTROSPECTION_PACKAGE}.proto"] = introspection_proto()
    return files
