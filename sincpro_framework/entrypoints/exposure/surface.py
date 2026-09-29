"""What a wire reads once the gateway resolved its surface: the mode, a context's group, one
operation's facts, the resolved binding — and the `Wire` port a project's own transport
implements to get decorators, groups, precedence and validation for free (PRD_14 §9).

Context: all of it is data of the application layer — the bus, the Command, the handler, their
schemas — so a wire translates it into its own concepts without the core knowing any of them.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import ConfigDict

from sincpro_framework.entrypoints.const import Layer, RunFn
from sincpro_framework.entrypoints.exposure.bindings import Binding
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.use_bus import UseFramework


class Exposure(StrEnum):
    """`DECLARED` publishes only the use cases with a binding for the gateway's wire — nothing
    is public by forgetting; `CATALOG` publishes every use case of every bus, and logs each one
    it publishes, so an automatic surface is never a surprise. `@internal` is on neither."""

    DECLARED = "declared"
    CATALOG = "catalog"


class Group(DataTransferObject):
    """The conventions of one bounded context on a wire — `None` leaves the wire's default:
    REST a prefix, tags and a version segment; JSON-RPC a namespace; gRPC a package."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alias: str
    prefix: str | None = None
    tags: tuple[str, ...] = ()
    version: str | None = None
    namespace: str | None = None
    package: str | None = None


class Operation(DataTransferObject):
    """One published use case, as facts a wire derives from: which context, which Command and
    handler, its kind and schemas, who may call it, and the bound `run` every wire calls — the
    bus, never `execute`, so no guard or stage is skipped."""

    model_config = ConfigDict(frozen=True)

    alias: str
    bus: UseFramework
    command: type
    handler: type
    layer: Layer
    description: str
    response: Any = None
    """What `execute` declares it answers — `None` when it answers nothing or declares
    nothing."""
    json_schema: dict[str, Any]
    response_json_schema: dict[str, Any] | None
    is_query: bool
    """A `Query` DTO: it reads — REST `GET`, MCP read-only."""
    idempotent: bool
    """`@idempotency.once` declared: a retry is safe."""
    access: str | None
    """What it declared — `public`, `authenticated`, the requirements — or `unguarded`; `None`
    when its bus is guarded and it declared nothing."""
    run: RunFn


class Resolved[B: Binding](DataTransferObject):
    """One operation with its binding resolved by precedence — what a wire builds from."""

    model_config = ConfigDict(frozen=True)

    operation: Operation
    group: Group
    binding: B


class ManifestEntry(DataTransferObject):
    """One operation of a surface, as an inventory reads it — JSON-safe, so a project snapshots
    `gateway.manifest()` in a test and every change to its public surface shows in the diff.
    """

    model_config = ConfigDict(frozen=True)

    wire: str
    context: str
    command: str
    """The Command, `module.Class`."""
    name: str
    """What the wire calls it — the wire's own name, or the Command's when the wire says none."""
    kind: str
    """`feature` or `app_service`, then `query` or `command`: `features.query`."""
    method: str | None
    """The binding's method when it has one — REST's verb, gRPC's method."""
    access: str | None
    deprecated: bool
    sunset: date | None
    replacement: str | None
    binding: dict[str, Any]
    """The resolved binding's own fields, JSON-safe."""


class Wire[B: Binding](ABC):
    """A transport the framework publishes on — a project implements one for its CLI, its
    webhook receiver, its queue consumer.

    Context: the gateway resolves the surface (internal, include/exclude, override/bind,
    decorator, group, derived — in that order) and validates what no wire can get wrong;
    the wire derives its defaults, adds its own clashes and builds what it serves."""

    binding: ClassVar[type[Binding]]
    """Its binding DTO — its `wire` is the name the registry keeps its bindings under."""

    @abstractmethod
    def derive(self, operation: Operation, group: Group) -> B:
        """The binding this wire gives an operation by itself — its group's conventions and
        what the facts prove; a declared field overrides it, a declared collection adds."""

    def name_of(self, resolved: Resolved[B]) -> str:
        """What the wire calls the operation — its manifest name. The Command's by default."""
        return resolved.operation.command.__name__

    @abstractmethod
    def validate(self, surface: Sequence[Resolved[B]]) -> list[str]:
        """What this wire refuses in the surface — two operations answering one name."""

    @abstractmethod
    def build(self, surface: Sequence[Resolved[B]]) -> Any:
        """What it serves — routes, a server, a command table — returned, never started."""
