"""The bindings: what one use case declares for one wire — frozen data, written by a decorator on
the handler or by the composition, read by the wire that publishes it.

Context: a field left `None` means *derive it* — the wire fills it from the facts (PRD_14 §6) and
the group's conventions (§5), so a binding says only the intent the framework cannot infer. The
fields are the protocol's concepts (a path, a status, a tool title), never a technology's type:
no Starlette, grpc or FastMCP object reaches a `services/` module through here.
"""

from datetime import date
from typing import ClassVar, Literal

from pydantic import ConfigDict

from sincpro_framework.exceptions import ExtensionRefused
from sincpro_framework.sincpro_abstractions import DataTransferObject

type HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
type RestBody = Literal["json", "merge-patch"]
type RestConcurrency = Literal["if-match"]


class Deprecation(DataTransferObject):
    """A binding on its way out: since when, when it stops being answered, and the DTO that
    replaces it. A `sunset` already past fails the build — it forces the removal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    since: date | None = None
    sunset: date | None = None
    replacement: type | None = None


class Binding(DataTransferObject):
    """What one use case declares for one wire. A project's own transport subclasses it with a
    `wire` of its own and records it with `declare(handler, binding)` (PRD_14 §9).

    Context: frozen, and a field it does not have is refused — `override(Command, stauts=201)`
    fails where it is written instead of publishing the default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    wire: ClassVar[str]
    """The wire this binding is for — one binding per wire per class."""

    deprecated: Deprecation | None = None


class RestBinding(Binding):
    """An HTTP operation: `method` + `path`, a template whose `{field}` names a Command field;
    `location` a template whose `{field}` names a response field (the `Location` of a 201)."""

    wire: ClassVar[str] = "rest"

    method: HttpMethod | None = None
    path: str | None = None
    status: int | None = None
    location: str | None = None
    tags: tuple[str, ...] = ()
    summary: str | None = None
    responses: tuple[type, ...] = ()
    """The domain errors to document for this operation."""
    body: RestBody | None = None
    concurrency: RestConcurrency | None = None


class RpcBinding(Binding):
    """A JSON-RPC method; `name` without the `rpc.` prefix the specification reserves."""

    wire: ClassVar[str] = "rpc"

    name: str | None = None
    notification: bool = False


class GrpcBinding(Binding):
    """A method of a gRPC service — `service` splits a context's one service (PRD_15 §3.2)."""

    wire: ClassVar[str] = "grpc"

    service: str | None = None
    method: str | None = None


class McpBinding(Binding):
    """An MCP tool. `read_only` is derived from the Command's kind and a declaration may not
    contradict it; `destructive` is the one hint the framework cannot prove — undeclared, a tool
    that writes stays the MCP default, destructive. Hints are never authorization."""

    wire: ClassVar[str] = "mcp"

    name: str | None = None
    title: str | None = None
    read_only: bool | None = None
    destructive: bool | None = None
    open_world: bool = False


class QueueBinding(Binding):
    """A use case run from a broker (PRD_15 §5.1): a Command it `consumes` from one channel,
    point to point, from the `producers` allowed; or a DomainEvent it `hears` in a consumer
    `group`."""

    wire: ClassVar[str] = "queue"

    kind: Literal["consumes", "hears"]
    channel: str | None = None
    producers: tuple[str, ...] = ()
    max_attempts: int | None = None
    concurrency: int | None = None
    group: str | None = None


BUILT_IN_BINDINGS: dict[str, type[Binding]] = {
    one.wire: one for one in (RestBinding, RpcBinding, GrpcBinding, McpBinding, QueueBinding)
}
"""The binding type of each wire the framework ships — what `override(Command, **fields)` is
checked against on a gateway that names its wire."""


class ExposureRefused(ExtensionRefused):
    """An exposure that cannot hold as declared — a second binding for one wire on a class, or
    a surface the build refuses (PRD_14 §7), every reason listed at once."""
