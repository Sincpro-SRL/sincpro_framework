"""The bus catalog as gRPC service names, method paths and `.proto` source.

Naming and rendering only — no `grpc` or `protobuf` import, so a build step can
export the `.proto` for the Go/TypeScript/Java clients without installing the
`[grpc]` extra, and `rpc/`'s method names and this module's paths stay two
renderings of the same catalog.

Payloads are `google.protobuf.Struct` on every method, not a generated message
per DTO. A typed message would need **stable field numbers**, and a Pydantic DTO
has no such registry: inserting a field in the middle of the class would renumber
everything after it and silently break every deployed client. `Struct` keys are
names, which is the same compatibility contract the JSON-RPC wire already has.
The field-level types still travel — as the JSON Schema in `Describe`.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any

from sincpro_framework.entrypoints.catalog import Catalog, PackedFeatureOrAppService
from sincpro_framework.entrypoints.const import Layer, Scalar
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.use_bus import UseFramework

PACKAGE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
STRUCT_TYPE = "google.protobuf.Struct"
STRUCT_IMPORT = "google/protobuf/struct.proto"
INTROSPECTION_PACKAGE = "sincpro"
INTROSPECTION_SERVICE = "Introspection"
DESCRIBE_METHOD = "Describe"
DESCRIBE_SERVICE = f"{INTROSPECTION_PACKAGE}.{INTROSPECTION_SERVICE}"
DESCRIBE_PATH = f"/{DESCRIBE_SERVICE}/{DESCRIBE_METHOD}"


class GrpcMethodSpec(DataTransferObject):
    """One Feature or ApplicationService as one unary gRPC method.

    `path` is what a client dials (`/{alias}.{Layer}/{DtoName}`); `operation`
    carries the bound `run` and the DTO's JSON Schema from the shared catalog.
    """

    alias: str
    layer: Layer
    service: str
    method: str
    path: str
    framework: UseFramework
    operation: PackedFeatureOrAppService


def validate_package(alias: str) -> str:
    """A gRPC package is a proto identifier: no hyphen, unlike a JSON-RPC alias."""
    if not PACKAGE_PATTERN.match(alias):
        raise ValueError(
            f"gRPC instance alias [{alias}] must match {PACKAGE_PATTERN.pattern} "
            "— a proto package allows no hyphen"
        )
    return alias


def service_name(layer: Layer | str) -> str:
    """`features` → `Features`, `app_services` → `AppServices`."""
    return "".join(part.capitalize() for part in str(layer).split("_"))


def index_specs(
    catalogs: Mapping[str, Catalog], layers: Iterable[str]
) -> dict[str, GrpcMethodSpec]:
    """Every JSON-safe Feature/ApplicationService of every instance, keyed by path."""
    allowed = set(layers)
    specs: dict[str, GrpcMethodSpec] = {}
    for alias, catalog in catalogs.items():
        for operation in catalog.get_scalar_use_cases(filter_binaries_schema=True):
            if operation.layer not in allowed:
                continue
            service = f"{alias}.{service_name(operation.layer)}"
            path = f"/{service}/{operation.name}"
            specs[path] = GrpcMethodSpec(
                alias=alias,
                layer=operation.layer,
                service=service,
                method=operation.name,
                path=path,
                framework=catalog.framework_instance,
                operation=operation,
            )
    return specs


def group_by_service(
    specs: Mapping[str, GrpcMethodSpec],
) -> dict[str, list[GrpcMethodSpec]]:
    """Specs grouped by full service name, insertion order preserved."""
    grouped: dict[str, list[GrpcMethodSpec]] = {}
    for spec in specs.values():
        grouped.setdefault(spec.service, []).append(spec)
    return grouped


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
                "alias": entries[0].alias,
                "layer": str(entries[0].layer),
                "methods": [
                    {
                        "name": spec.method,
                        "path": spec.path,
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


def _service_block(service: str, entries: list[GrpcMethodSpec]) -> list[str]:
    lines = [
        *_comment(
            f"{entries[0].layer} registered on UseFramework instance "
            f"[{entries[0].framework._logger_name}].",
            "",
        ),
        f"service {service.split('.', 1)[1]} {{",
    ]
    for spec in entries:
        lines.extend(_comment(spec.operation.description, "  "))
        lines.extend(_comment(_result_hint(spec), "  "))
        lines.append(
            f"  rpc {spec.method}({STRUCT_TYPE}) returns ({STRUCT_TYPE});",
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
    """`{filename: source}` — one file per instance alias, plus `sincpro.proto`.

    One package per file, because proto allows exactly one. The client team
    generates stubs from these; the server serves precisely what they describe.
    """
    per_package: dict[str, list[list[str]]] = {}
    for service, entries in group_by_service(specs).items():
        package = service.split(".", 1)[0]
        per_package.setdefault(package, []).append(_service_block(service, entries))
    files = {
        f"{package}.proto": _file(package, blocks) for package, blocks in per_package.items()
    }
    files[f"{INTROSPECTION_PACKAGE}.proto"] = introspection_proto()
    return files
