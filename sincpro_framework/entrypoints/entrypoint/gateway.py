"""`Gateway`: what every entrypoint shares — the buses it serves, each as a filtered catalog, and
the one way to say which use cases a wire publishes.

    RestGateway([billing, sales])                          # automatic: every bus, every use case
    RestGateway([billing, sales], layers=("app_services",))   # one layer only
    RestGateway().add(billing, exclude=[CommandReconcile]).add("ventas", sales, include=[...])

Context: the same on every wire — JSON-RPC, gRPC, MCP, REST — so exposing a bus is learned once.
A list of buses is the automatic mode: each is published under its own name, made fit for the
wire (`sincpro-billing` is `sincpro_billing` for a gRPC package). `add` is the fine-grained one:
an alias of the caller's, `include` / `exclude` by Command or name, `wrap` to decorate one use
case's run. A use case marked `entrypoints.internal` is on no wire, whatever is included. What a
gateway builds — an ASGI app, its routes, a gRPC server, a FastMCP server — is returned, never
started, so a project adds its middleware, interceptors and plugins to it.

Declared exposure (PRD_14): a gateway that names its `wire` — a class attribute its wire sets, or
a `Wire` port of the project's — resolves a **surface**: in `Exposure.DECLARED` (the default)
only the use cases bound for that wire, in `Exposure.CATALOG` every one. Each binding is resolved
by one precedence, highest first: `@internal` · `exclude` / `include` · `override` / `bind` (a
field-level merge, the last call wins) · the decorator · the group · what is derived. The surface
is validated before anything is served, and every reason it is refused is said at once — fail
closed. A gateway that names no wire keeps the catalog behaviour of PRD_12, unchanged.
"""

import dataclasses
import re
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ValidationError

from sincpro_framework.auth.entrypoint.access_control import access_control_of
from sincpro_framework.common.naming import registered_name
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.query import Query
from sincpro_framework.entrypoints.domain.bindings import (
    BUILT_IN_BINDINGS,
    Binding,
    McpBinding,
    QueueBinding,
    RestBinding,
    RpcBinding,
)
from sincpro_framework.entrypoints.domain.layers import Layer, Wrapper
from sincpro_framework.entrypoints.domain.surface import (
    Exposure,
    Group,
    ManifestEntry,
    Operation,
    Resolved,
    Wire,
)
from sincpro_framework.entrypoints.entrypoint.catalog import (
    Catalog,
    PackedFeatureOrAppService,
)
from sincpro_framework.entrypoints.entrypoint.internal import is_internal
from sincpro_framework.entrypoints.infrastructure.registry import registry
from sincpro_framework.exceptions import ProgrammingError
from sincpro_framework.introspection import inspector
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework

DEFAULT_LAYERS = (Layer.APP_SERVICES, Layer.FEATURES)
UNGUARDED = "unguarded"
TEMPLATE_FIELD = re.compile(r"\{([^{}]+)\}")

type Buses = Mapping[str, UseFramework] | Iterable[UseFramework]
"""`{"billing": billing}` names each bus; `[billing, sales]` publishes each under its own name."""

type Published = tuple[str, UseFramework, PackedFeatureOrAppService]


def _fields_of(cls: Any) -> set[str] | None:
    """A DTO's, a dataclass's field names — `None` for what has no fields (a list, a scalar)."""
    if isinstance(cls, type) and issubclass(cls, BaseModel):
        return set(cls.model_fields)
    if isinstance(cls, type) and dataclasses.is_dataclass(cls):
        return {one.name for one in dataclasses.fields(cls)}
    return None


def _required_fields_of(cls: type) -> set[str]:
    if isinstance(cls, type) and issubclass(cls, BaseModel):
        return {name for name, info in cls.model_fields.items() if info.is_required()}
    return set()


def _json_safe(value: Any) -> Any:
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    if isinstance(value, tuple | list):
        return [_json_safe(one) for one in value]
    if isinstance(value, BaseModel):
        return {name: _json_safe(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, date):
        return value.isoformat()
    return value


def _fields_set(binding: Binding) -> dict[str, Any]:
    return {name: getattr(binding, name) for name in binding.model_fields_set}


def _merged(baseline: Binding | None, declared: Mapping[str, Any]) -> dict[str, Any]:
    """The declared fields over what the wire derived: a scalar declared wins, a collection
    declared adds to the derived one (a group's tags) — the closest layer wins, collections
    are additive."""
    merged: dict[str, Any] = {} if baseline is None else _fields_set(baseline)
    for name, value in declared.items():
        held = merged.get(name)
        if isinstance(value, tuple) and isinstance(held, tuple):
            merged[name] = tuple(dict.fromkeys((*held, *value)))
        else:
            merged[name] = value
    return merged


def _rest_problems(
    where: str, command: type, response: Any, binding: RestBinding
) -> list[str]:
    problems: list[str] = []
    fields = _fields_of(command) or set()
    in_path = TEMPLATE_FIELD.findall(binding.path or "")
    for name in in_path:
        if name not in fields:
            problems.append(
                f"{where}: path {binding.path} names {{{name}}}, which is not a field of "
                f"{command.__name__} — it has {', '.join(sorted(fields)) or 'none'}"
            )
    answered = _fields_of(response)
    for name in TEMPLATE_FIELD.findall(binding.location or ""):
        if answered is None or name not in answered:
            problems.append(
                f"{where}: location {binding.location} names {{{name}}}, which is not a field "
                f"of what it answers ({getattr(response, "__name__", str(response))})"
            )
    if binding.method == "GET" and not (
        isinstance(command, type) and issubclass(command, Query)
    ):
        problems.append(f"{where}: GET on a Command that is not a Query — a read is a Query")
    answers_no_body = binding.method == "DELETE" or binding.status == 204
    if answers_no_body and response not in (None, type(None)):
        problems.append(
            f"{where}: DELETE / 204 answers no body, but execute answers "
            f"{getattr(response, "__name__", str(response))} — answer None"
        )
    if binding.method == "PATCH" and binding.body != "merge-patch":
        required = _required_fields_of(command) - set(in_path)
        if required:
            problems.append(
                f"{where}: PATCH changes part of a resource — declare body='merge-patch' or "
                f"make {', '.join(sorted(required))} optional"
            )
    return problems


def _binding_problems(
    command: type, response: Any, binding: Binding, today: date
) -> list[str]:
    """What a binding claims that the facts of its use case contradict (PRD_14 §7, PRD_15
    §2.2) — the same whatever wire builds it."""
    where = f"{command.__name__} ({binding.wire})"
    problems: list[str] = []
    deprecation = binding.deprecated
    if deprecation is not None and deprecation.sunset is not None:
        if deprecation.sunset < today:
            problems.append(
                f"{where}: its sunset {deprecation.sunset} is past — remove the binding"
            )
    is_query = isinstance(command, type) and issubclass(command, Query)
    if isinstance(binding, RestBinding):
        problems += _rest_problems(where, command, response, binding)
    elif isinstance(binding, McpBinding):
        if binding.read_only is True and not is_query:
            problems.append(f"{where}: read_only=True on a Command that is not a Query")
        if binding.read_only is False and is_query:
            problems.append(f"{where}: read_only=False on a Query — a Query only reads")
    elif isinstance(binding, RpcBinding):
        if binding.name is not None and binding.name.startswith("rpc."):
            problems.append(f"{where}: {binding.name} — the rpc. prefix is reserved")
    elif isinstance(binding, QueueBinding):
        is_event = isinstance(command, type) and issubclass(command, DomainEvent)
        if binding.kind == "consumes" and is_event:
            problems.append(
                f"{where}: consumes a DomainEvent — an event is heard: queue.hears"
            )
        if binding.kind == "consumes" and not binding.channel:
            problems.append(f"{where}: consumes from no channel — name its channel")
        if binding.kind == "hears" and not is_event:
            problems.append(
                f"{where}: hears a Command — a Command is consumed: queue.consumes(channel)"
            )
    return problems


def _stage_problems(command: type, handler: type) -> list[str]:
    """The combinations of stages PRD_14 §10 refuses. `@caching.keeps` on a non-Query joins
    here once it exists as a stage — today it does not."""
    from sincpro_framework.data_layer.caching import declares_once

    if declares_once(handler) and isinstance(command, type) and issubclass(command, Query):
        return [
            f"{command.__name__}: @idempotency.once on a Query — a read changes nothing, there "
            "is nothing to run once"
        ]
    return []


def _responses_of(bus: UseFramework) -> dict[type, Any]:
    """What each handler of `bus` declares it answers, by Command — excluded and internal ones
    included, since their declarations are validated too."""
    described = {**inspector.features(bus), **inspector.app_services(bus)}
    return {metadata.dto: metadata.response for metadata in described.values()}


class Gateway:
    wire: ClassVar[str | None] = None
    """The wire whose bindings this gateway publishes — `"rest"`, `"rpc"`, `"grpc"`, `"mcp"`,
    `"queue"` — set by each wire's gateway once it resolves a declared surface. `None` keeps the
    catalog of PRD_12, unchanged."""

    def __init__(
        self,
        instances: Buses | None = None,
        layers: Iterable[str] = DEFAULT_LAYERS,
        title: str = "sincpro",
        version: str = "1.0.0",
        exposure: Exposure = Exposure.DECLARED,
        unguarded: bool = False,
        port: Wire[Any] | None = None,
    ) -> None:
        """`exposure` is the mode of a gateway that names a wire; `unguarded=True` says its
        buses have no `AccessControl` on purpose; `port` is a project's own transport."""
        self._catalogs: dict[str, Catalog] = {}
        self._layers = tuple(layers)
        self._title = title
        self._version = version
        self._exposure = exposure
        self._unguarded = unguarded
        self._port = port
        self._groups: dict[str, Group] = {}
        self._composition: list[tuple[type, Binding | dict[str, Any]]] = []
        """Each `override` (its fields) and `bind` (its binding), in the order called."""
        self._resolved: list[tuple[Resolved[Any], Published]] | None = None
        self._resolved_revisions: tuple[int, ...] = ()
        named = (
            instances.items()
            if isinstance(instances, Mapping)
            else ((None, one) for one in instances or ())
        )
        for alias, framework_instance in named:
            self.add(alias or framework_instance, framework_instance)

    def validate_alias(self, alias: str) -> str:
        """What this wire accepts as a bus's alias — refused with the rule it breaks."""
        return alias

    def alias_for(self, framework_instance: UseFramework) -> str:
        """The alias a bus added by itself is published under — its name, fit for the wire."""
        return framework_instance.name

    def added(self, alias: str, framework_instance: UseFramework) -> None:
        """Called once a bus is added — what a wire checks or warns about one."""

    def add(
        self,
        alias: str | UseFramework,
        framework_instance: UseFramework | None = None,
        include: Iterable[type | str] | None = None,
        exclude: Iterable[type | str] | None = None,
        wrap: Mapping[type | str, Wrapper] | None = None,
    ) -> Self:
        """Publish a bus — `add(billing)` under its own name, `add("ventas", sales)` under one of
        the caller's — narrowed by `include` / `exclude` and with `wrap`ped runs."""
        if isinstance(alias, UseFramework):
            framework_instance, alias = alias, self.alias_for(alias)
        if framework_instance is None:
            raise TypeError(f"add({alias!r}) names no bus: add({alias!r}, bus) or add(bus)")
        catalog = Catalog(framework_instance)
        if include is not None:
            catalog.include(*include)
        if exclude is not None:
            catalog.exclude(*exclude)
        for dto, wrapper in (wrap or {}).items():
            catalog.wrap(dto, wrapper)
        validated = self.validate_alias(alias)
        self._catalogs[validated] = catalog
        self._resolved = None
        self.added(validated, framework_instance)
        return self

    @property
    def catalogs(self) -> Mapping[str, Catalog]:
        return dict(self._catalogs)

    def buses(self) -> list[UseFramework]:
        return [catalog.framework_instance for catalog in self._catalogs.values()]

    @property
    def carries_bytes(self) -> bool:
        """Whether this gateway's wire holds raw bytes — its port says; a gateway with no port
        speaks JSON."""
        return self._port is not None and self._port.carries_bytes

    def _published(self) -> list[Published]:
        return [
            (alias, catalog.framework_instance, operation)
            for alias, catalog in self._catalogs.items()
            for operation in catalog.get_scalar_use_cases(
                filter_binaries_schema=not self.carries_bytes
            )
            if operation.layer in self._layers
        ]

    def operations(self) -> list[Published]:
        """Every published use case, with the alias and bus it belongs to, in the layers asked —
        on a gateway that names a wire, only its validated surface."""
        if self.wire_name is None:
            return self._published()
        return [published for _, published in self._resolve_validated()]

    def is_healthy(self) -> bool:
        """Every bus is still built — what every wire's health check answers."""
        return all(one.is_ready for one in self.buses())

    # Declared exposure — the composition's word (PRD_14 §5, §7)

    @property
    def wire_name(self) -> str | None:
        """The wire this gateway resolves bindings for — its port's, else its class's."""
        return self._port.binding.wire if self._port is not None else type(self).wire

    @property
    def exposure(self) -> Exposure:
        return self._exposure

    @property
    def groups(self) -> Mapping[str, Group]:
        """Each context's group, by alias — `Group(alias=...)`, all defaults, where none was
        set."""
        return {
            alias: self._groups.get(alias, Group(alias=alias)) for alias in self._catalogs
        }

    def _binding_type(self) -> type[Binding]:
        if self._port is not None:
            return self._port.binding
        wire = type(self).wire
        if wire is None or wire not in BUILT_IN_BINDINGS:
            raise ProgrammingError(
                f"{type(self).__name__} names no wire the framework knows ({wire!r}): give it "
                "a Wire port, or set its `wire`"
            )
        return BUILT_IN_BINDINGS[wire]

    def group(
        self,
        bus: UseFramework | str,
        prefix: str | None = None,
        tags: tuple[str, ...] = (),
        version: str | None = None,
        namespace: str | None = None,
        package: str | None = None,
    ) -> Self:
        """The conventions of one bounded context on this wire — the bus, or its alias; every
        alias it is added under. What a field leaves `None` is the wire's default."""
        aliases = [
            alias
            for alias, catalog in self._catalogs.items()
            if alias == bus or catalog.framework_instance is bus
        ]
        if not aliases:
            named = bus if isinstance(bus, str) else bus.name
            raise ProgrammingError(
                f"group({named!r}): no bus of this gateway is {named!r} — add it first; it has "
                f"{', '.join(self._catalogs) or 'none'}"
            )
        for alias in aliases:
            self._groups[alias] = Group(
                alias=alias,
                prefix=prefix,
                tags=tags,
                version=version,
                namespace=namespace,
                package=package,
            )
        self._resolved = None
        return self

    def override(self, target: type, **fields: Any) -> Self:
        """Change fields of the binding `target` — a Command or its handler — has on this wire,
        over its decorator's; the last call wins. It shapes, it never publishes: `bind` does.
        """
        binding_type = self._binding_type()
        unknown = sorted(set(fields) - set(binding_type.model_fields))
        if unknown:
            raise ProgrammingError(
                f"override({target.__name__}): {binding_type.__name__} has no "
                f"{', '.join(unknown)}"
            )
        self._composition.append((target, dict(fields)))
        self._resolved = None
        return self

    def bind(self, target: type, binding: Binding) -> Self:
        """Publish `target` — a Command or its handler — on this wire with `binding`, merged
        field by field over its decorator's; the last call wins."""
        binding_type = self._binding_type()
        if not isinstance(binding, binding_type):
            raise ProgrammingError(
                f"bind({target.__name__}, {type(binding).__name__}): this gateway publishes "
                f"{binding_type.wire} — bind a {binding_type.__name__}"
            )
        self._composition.append((target, binding))
        self._resolved = None
        return self

    def _answered(self, target: type) -> bool:
        """A Command a bus of this gateway answers, or the handler answering one."""
        return any(
            target is command or target is handler
            for bus in self.buses()
            for command, handler in bus.handlers().items()
        )

    def _composed_for(
        self, command: type, handler: type
    ) -> tuple[list[dict[str, Any]], bool]:
        """The composition's layers for one use case, in call order, and whether a `bind` is
        among them."""
        layers: list[dict[str, Any]] = []
        bound = False
        for target, entry in self._composition:
            if target is not command and target is not handler:
                continue
            if isinstance(entry, Binding):
                bound = True
                layers.append(_fields_set(entry))
            else:
                layers.append(entry)
        return layers, bound

    def _access_of(self, bus: UseFramework, command: type) -> str | None:
        guard = access_control_of(bus)
        if guard is None:
            return UNGUARDED
        declaration = guard.requirements_of(command)
        return None if declaration is None else str(declaration)

    def _operation(self, published: Published, handler: type) -> Operation:
        from sincpro_framework.data_layer.caching import declares_once

        alias, bus, packed = published
        return Operation(
            alias=alias,
            bus=bus,
            command=packed.dto,
            handler=handler,
            layer=packed.layer,
            description=packed.description,
            response=packed.response,
            json_schema=packed.json_schema,
            response_json_schema=packed.response_json_schema,
            is_query=(isinstance(packed.dto, type) and issubclass(packed.dto, Query)),
            idempotent=declares_once(handler),
            access=self._access_of(bus, packed.dto),
            run=packed.run,
        )

    def _access_problems(self, operation: Operation, wire: str) -> list[str]:
        """The fallback rule: what is reachable says who may call it (PRD_14 §7)."""
        where = f"{operation.command.__name__} ({wire})"
        if operation.access is None:
            return [
                f"{where}: published on a bus guarded by AccessControl, but declares neither "
                "@auth.requires nor @auth.public — say who may call it"
            ]
        if operation.access == UNGUARDED and not self._unguarded:
            return [
                f"{where}: its bus '{operation.bus.name}' has no AccessControl — guard it, or "
                "tell the gateway unguarded=True"
            ]
        return []

    def _published_by_default(self, published: Published) -> bool:
        """A use case this wire publishes with no binding in DECLARED mode. None on a wire an
        outsider calls; the queue wire hears every registered DomainEvent (PRD_15 §5.1)."""
        return False

    def _resolve_one(
        self, published: Published, wire: str, today: date, problems: list[str]
    ) -> Resolved[Any] | None:
        """One use case's binding on this wire, by precedence — `None` when it is not on it.

        1. `@internal` and `exclude` / `include` already left it out of the catalog.
        2. The decorator's binding, then the composition's layers in call order, by field.
        3. Not bound in DECLARED mode: not published — an `override` alone is refused — unless
           the wire publishes it by default (`_published_by_default`).
        4. Final: the declared fields over what the port derives from the group and the facts,
           validated against the facts and the fallback access rule.
        """
        alias, bus, packed = published
        command = packed.dto
        handler = packed.handler or bus.handler_of(command) or command
        declared = registry.of(handler, bus.replaced_for(command)).get(wire)
        layers, bound = self._composed_for(command, handler)
        if declared is not None:
            layers.insert(0, _fields_set(declared))
        if (
            self._exposure == Exposure.DECLARED
            and declared is None
            and not bound
            and not self._published_by_default(published)
        ):
            if layers:
                problems.append(
                    f"{command.__name__}: override on {wire}, but it has no {wire} binding — "
                    "override shapes a binding, bind publishes one"
                )
            return None
        fields: dict[str, Any] = {}
        for layer in layers:
            fields.update(layer)
        operation = self._operation(published, handler)
        group = self._groups.get(alias, Group(alias=alias))
        baseline = self._port.derive(operation, group) if self._port is not None else None
        try:
            binding = self._binding_type()(**_merged(baseline, fields))
        except ValidationError as refused:
            problems.append(
                f"{command.__name__} ({wire}): its binding does not hold — {refused}"
            )
            return None
        problems.extend(_binding_problems(command, packed.response, binding, today))
        problems.extend(self._access_problems(operation, wire))
        return Resolved[Any](operation=operation, group=group, binding=binding)

    def _declaration_problems(self, today: date) -> list[str]:
        """What is wrong whatever wire builds it: every binding a handler of these buses declares
        for another wire, `@internal` with a binding, the stage combinations, and a composition naming a
        Command no bus of this gateway answers."""
        problems: list[str] = []
        for bus in dict.fromkeys(self.buses()):
            responses = _responses_of(bus)
            for command, handler in bus.handlers().items():
                declared = registry.of(handler, bus.replaced_for(command))
                _, bound = self._composed_for(command, handler)
                if is_internal(command, handler) and (declared or bound):
                    wires = ", ".join(sorted(declared)) or str(self.wire_name)
                    problems.append(
                        f"{command.__name__}: @internal and bound on {wires} — never "
                        "published, and published: drop one"
                    )
                for wire, binding in declared.items():
                    if wire == self.wire_name:
                        continue  # checked once resolved, the composition's word included
                    problems += _binding_problems(
                        command, responses.get(command), binding, today
                    )
                problems += _stage_problems(command, handler)
        for target, _ in self._composition:
            if not self._answered(target):
                problems.append(
                    f"override/bind of {target.__name__}: no bus of this gateway answers it"
                )
        return problems

    def _binary_problems(self, wire: str) -> list[str]:
        """Every use case bound for this wire whose Command carries bytes the wire cannot hold.

        Context: what a payload holds is the wire's (`Wire.carries_bytes`). On a wire that does
        not, a `bytes` DTO is left out, so its binding would build no route, tool or method — a
        declaration that silently does nothing.

        1. A wire that carries bytes publishes them: nothing to refuse.
        2. Only what the gateway would publish: `include` / `exclude`, `@internal` and the layers
           already narrowed the catalog.
        3. Final: refused when the decorator or a `bind` puts it on this wire.
        """
        problems: list[str] = []
        if self.carries_bytes:
            return problems
        for catalog in self._catalogs.values():
            bus = catalog.framework_instance
            json_safe = {
                packed.dto
                for packed in catalog.get_scalar_use_cases(filter_binaries_schema=True)
            }
            for packed in catalog.get_scalar_use_cases():
                if packed.dto in json_safe or packed.layer not in self._layers:
                    continue
                command = packed.dto
                handler = packed.handler or bus.handler_of(command) or command
                declared = registry.of(handler, bus.replaced_for(command)).get(wire)
                _, bound = self._composed_for(command, handler)
                if declared is None and not bound:
                    continue
                problems.append(
                    f"{command.__name__} ({wire}): bound, but it has a bytes field and this "
                    f"{wire} wire carries JSON only — nothing would be built for it. Drop the "
                    f"{wire} binding and publish it with an endpoint of the project's that decodes "
                    "the request and calls the bus (REST: `bus_call`), or on a wire that carries "
                    "bytes"
                )
        return problems

    def _resolve(self) -> tuple[list[tuple[Resolved[Any], Published]], list[str]]:
        wire = self.wire_name
        if wire is None:
            raise ProgrammingError(
                f"{type(self).__name__} names no wire: it publishes its catalog as PRD_12 did, "
                "and has no declared surface to resolve"
            )
        today = date.today()
        problems = self._declaration_problems(today) + self._binary_problems(wire)
        resolved: list[tuple[Resolved[Any], Published]] = []
        for published in self._published():
            one = self._resolve_one(published, wire, today, problems)
            if one is not None:
                resolved.append((one, published))
        if self._port is not None:
            problems += self._port.validate([one for one, _ in resolved])
        return resolved, list(dict.fromkeys(problems))

    def _resolve_validated(self) -> list[tuple[Resolved[Any], Published]]:
        revisions = tuple(catalog.revision for catalog in self._catalogs.values())
        if self._resolved is not None and revisions == self._resolved_revisions:
            return self._resolved
        resolved, problems = self._resolve()
        if problems:
            raise ProgrammingError(
                f"{type(self).__name__} ({self.wire_name}) refuses its surface:\n- "
                + "\n- ".join(problems)
            )
        self._resolved = resolved
        self._resolved_revisions = revisions
        if self._exposure == Exposure.CATALOG:
            for entry in self.manifest():
                logger.info(
                    f"{entry.wire}: {entry.name} published by catalog — {entry.command}, "
                    f"access {entry.access}"
                )
        return resolved

    def verify(self) -> list[str]:
        """Every reason the surface would be refused — empty when it builds. Raises nothing."""
        return self._resolve()[1]

    def surface(self) -> list[Resolved[Any]]:
        """The validated surface — each published use case with its resolved binding. Refused
        with `ProgrammingError`, every reason listed, when it does not hold."""
        return [one for one, _ in self._resolve_validated()]

    def build(self) -> Any:
        """The surface validated, then built by the port — or the surface itself when the
        gateway has no port and its wire builds from `surface()`."""
        surface = self.surface()
        return surface if self._port is None else self._port.build(surface)

    def _entry(self, resolved: Resolved[Any]) -> ManifestEntry:
        operation, binding = resolved.operation, resolved.binding
        deprecation = binding.deprecated
        replacement = None if deprecation is None else deprecation.replacement
        return ManifestEntry(
            wire=binding.wire,
            context=operation.alias,
            command=registered_name(operation.command, operation.bus.name),
            name=(
                self._port.name_of(resolved)
                if self._port is not None
                else operation.command.__name__
            ),
            kind=f"{operation.layer}.{'query' if operation.is_query else 'command'}",
            method=getattr(binding, "method", None),
            access=operation.access,
            deprecated=deprecation is not None,
            sunset=None if deprecation is None else deprecation.sunset,
            replacement=(
                None
                if replacement is None
                else registered_name(replacement, operation.bus.name)
            ),
            binding={
                name: _json_safe(getattr(binding, name))
                for name in type(binding).model_fields
                if name != "deprecated"
            },
        )

    def manifest(self) -> tuple[ManifestEntry, ...]:
        """The surface as data — per operation: wire, name, kind, method, access, deprecation —
        for an inventory and a CI snapshot, by context and name so the order never moves."""
        entries = (self._entry(resolved) for resolved, _ in self._resolve_validated())
        return tuple(sorted(entries, key=lambda one: (one.wire, one.context, one.name)))
