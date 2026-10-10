"""The gRPC names of a declared surface, AIP's way (PRD_15 §3.2) — and the `Wire` that derives,
validates and builds them.

    package   {alias}.v{major}          billing.v1          (the group's `package` / `version`)
    service   {Alias}Service            BillingService      (`@grpc(service=...)` splits one)
    method    the DTO without its kind  CommandIssueInvoice → IssueInvoice
    path      /{package}.{service}/{method}

Context: the layer (`Features`, `AppServices`) never reaches a public name — promoting a Feature
to an ApplicationService is an internal change, and it used to rename a method every client had
generated a stub for. Two use cases answering one path after the kind is stripped fail the
build instead of one silently shadowing the other. No `grpc` or `protobuf` import: the names and
the `.proto` export work without the `[grpc]` extra.
"""

import re
from collections.abc import Sequence

from sincpro_framework.entrypoints.adapters.grpc.proto import (
    RESERVED_SERVICES,
    GrpcMethodSpec,
    validate_package,
)
from sincpro_framework.entrypoints.domain.bindings import GrpcBinding
from sincpro_framework.entrypoints.domain.surface import Group, Operation, Resolved, Wire

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VERSION = re.compile(r"^v?(\d+)(\.\d+)*$")
VERSION_SEGMENT = re.compile(r"\.v\d+$")
KINDS = ("Command", "Query")
"""The prefixes a DTO name carries by convention and a public method name does not."""
DEFAULT_MAJOR = 1


def alias_of(name: str) -> str:
    """A bus's own name as a proto package segment — `sincpro-billing` is `sincpro_billing`."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def pascal(name: str) -> str:
    """`sales_orders` → `SalesOrders`, `billing` → `Billing`; an inner capital is kept."""
    return "".join(part[:1].upper() + part[1:] for part in name.split("_") if part)


def method_name(command: type) -> str:
    """The DTO's name without `Command`/`Query`, PascalCase — `QueryInvoice` → `Invoice`.

    Context: stripped only when a word follows (`Commander` stays, `Query` alone stays)."""
    name = command.__name__
    for kind in KINDS:
        rest = name[len(kind) :]
        if name.startswith(kind) and rest[:1].isupper():
            name = rest
            break
    return pascal(name)


def service_name(alias: str) -> str:
    return f"{pascal(alias)}Service"


def major_of(version: str | None) -> int:
    """`v2`, `2` or `2.1` → 2; `None` → 1. Refused when it is no version."""
    if version is None:
        return DEFAULT_MAJOR
    found = VERSION.match(version)
    if found is None:
        raise ValueError(f"version {version!r} is no version — say v1, v2, ...")
    return int(found.group(1))


def package_of(group: Group) -> str:
    """`{alias}.v{major}` — the group's `package` in place of the alias, taken as it is when it
    already ends with its version (`acme.billing.v2`)."""
    base = group.package or group.alias
    if VERSION_SEGMENT.search(base):
        return base
    return f"{base}.v{major_of(group.version)}"


def default_package(bus_name: str) -> str:
    """The package a bus added by itself is served under — what `bus_call` names as the domain of
    its errors when the servicer says none."""
    return package_of(Group(alias=alias_of(bus_name)))


def path_of(package: str, service: str, method: str) -> str:
    return f"/{package}.{service}/{method}"


class GrpcWire(Wire[GrpcBinding]):
    """gRPC's `Wire`: derives the AIP names, refuses what cannot be served, builds the method
    table `GrpcGateway` serves. A project changes a naming rule by subclassing it and handing it
    to `GrpcGateway(port=...)`."""

    binding = GrpcBinding

    def derive(self, operation: Operation, group: Group) -> GrpcBinding:
        return GrpcBinding(
            service=service_name(group.alias), method=method_name(operation.command)
        )

    def path(self, resolved: Resolved[GrpcBinding]) -> str:
        binding = resolved.binding
        return path_of(package_of(resolved.group), str(binding.service), str(binding.method))

    def name_of(self, resolved: Resolved[GrpcBinding]) -> str:
        return self.path(resolved)

    def _name_problems(self, resolved: Resolved[GrpcBinding]) -> list[str]:
        where = f"{resolved.operation.command.__name__} (grpc)"
        problems: list[str] = []
        try:
            validate_package(package_of(resolved.group))
        except ValueError as refused:
            problems.append(f"{where}: {refused}")
        binding = resolved.binding
        for field, value in (("service", binding.service), ("method", binding.method)):
            if value is None or not IDENTIFIER.match(value):
                problems.append(
                    f"{where}: {field} {value!r} is no proto identifier — letters, digits and "
                    "_ only, not starting with a digit"
                )
        return problems

    def validate(self, surface: Sequence[Resolved[GrpcBinding]]) -> list[str]:
        """Every name a proto identifier, no path answered twice, none of the framework's own
        services taken."""
        problems: list[str] = []
        answering: dict[str, list[str]] = {}
        for resolved in surface:
            named = self._name_problems(resolved)
            problems += named
            if named:
                continue
            path = self.path(resolved)
            answering.setdefault(path, []).append(resolved.operation.command.__name__)
            service = path[1:].split("/", 1)[0]
            if service in RESERVED_SERVICES:
                problems.append(
                    f"{resolved.operation.command.__name__} (grpc): {service} is the "
                    "framework's own service — choose another package or service"
                )
        for path, commands in answering.items():
            if len(commands) > 1:
                problems.append(
                    f"{path} is answered by {', '.join(commands)} — once Command/Query is "
                    "stripped they share one name: give one of them @grpc(method=...)"
                )
        return problems

    def build(self, surface: Sequence[Resolved[GrpcBinding]]) -> dict[str, GrpcMethodSpec]:
        """`{path: GrpcMethodSpec}` — what the server mounts, reflection and `Describe` list and
        the `.proto` export renders."""
        specs: dict[str, GrpcMethodSpec] = {}
        for resolved in surface:
            package = package_of(resolved.group)
            path = self.path(resolved)
            specs[path] = GrpcMethodSpec(
                alias=resolved.group.alias,
                package=package,
                service=f"{package}.{resolved.binding.service}",
                method=str(resolved.binding.method),
                path=path,
                framework=resolved.operation.bus,
                operation=resolved.operation,
                deprecated=resolved.binding.deprecated is not None,
            )
        return specs
