"""The JSON-RPC wire on declared exposure (PRD_14 §9, PRD_15 §4): how a method is named, what
the build refuses, what is served — and `dispatch`, the one call path every mode runs.

    billing.issue_invoice          # {namespace}.{operation}
    billing.v2.issue_invoice       # a breaking version in the namespace

Context: the namespace is the context's group (`group(bus, namespace=...)`, the alias by
default); the operation is the DTO's name without `Command`/`Query`, in snake_case. No layer
is in any name: promoting a Feature to an ApplicationService changes no public method. A
project's own route calls `dispatch(gateway.surface(), payload, ...)` and gets exactly what the
gateway's route answers — limits, notifications, errors — because the gateway's route calls it
too.
"""

import json
import re
from collections.abc import Mapping, Sequence
from functools import cached_property
from typing import Any

from sincpro_framework.auth.domain import Credentials
from sincpro_framework.auth.transports import challenges_of
from sincpro_framework.entrypoints.exposure import (
    Group,
    Operation,
    Resolved,
    RpcBinding,
    Wire,
)
from sincpro_framework.entrypoints.rpc.errors import UNAUTHENTICATED
from sincpro_framework.entrypoints.rpc.jrpc import (
    DEFAULT_MAX_BATCH_SIZE,
    DEFAULT_MAX_BODY_BYTES,
    MethodIndex,
    body_too_large,
    handle_payload,
    merge_http_context,
    method_object,
    openrpc_document,
    parse_error,
)
from sincpro_framework.use_bus import UseFramework

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
"""`namespace.operation` — dotted snake_case segments, at least two."""
LAYER_PREFIX = re.compile(r"^(Command|Query)(?=[A-Z0-9])")
WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
STATUS_BY_REASON = {"BODY_TOO_LARGE": 413, "UNSUPPORTED_MEDIA_TYPE": 415}


def snake_case(name: str) -> str:
    return WORD_BOUNDARY.sub("_", name).replace("-", "_").lower()


def operation_name(dto_name: str) -> str:
    """`CommandIssueInvoice` → `issue_invoice`; a name that is only `Command` stays itself."""
    return snake_case(LAYER_PREFIX.sub("", dto_name))


def positive_limit(name: str, value: int) -> int:
    if value < 1:
        raise ValueError(f"RPC [{name}] must be at least 1, got {value}")
    return value


def method_name_of(resolved: Resolved[RpcBinding]) -> str:
    name = resolved.binding.name
    if name is None:
        raise ValueError(f"{resolved.operation.command.__name__} has no JSON-RPC name")
    return name


class RpcSurface:
    """What the JSON-RPC wire serves: the methods by name, the limits, the OpenRPC document
    (built once, on first use) — what `dispatch` runs against."""

    def __init__(
        self,
        surface: Sequence[Resolved[RpcBinding]],
        *,
        title: str = "sincpro-rpc",
        version: str = "1.0.0",
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        self.methods: MethodIndex = {method_name_of(one): one for one in surface}
        self.title = title
        self.version = version
        self.max_batch_size = positive_limit("max_batch_size", max_batch_size)
        self.max_body_bytes = positive_limit("max_body_bytes", max_body_bytes)

    @cached_property
    def document(self) -> dict[str, Any]:
        schemas: dict[str, Any] = {}
        published = [method_object(name, one, schemas) for name, one in self.methods.items()]
        return openrpc_document(
            self.title,
            published,
            version=self.version,
            schemas=schemas,
            max_batch_size=self.max_batch_size,
            max_body_bytes=self.max_body_bytes,
        )

    def discover(self) -> dict[str, Any]:
        return self.document

    def buses(self) -> list[UseFramework]:
        return list(dict.fromkeys(one.operation.bus for one in self.methods.values()))

    @cached_property
    def challenges(self) -> tuple[str, ...]:
        """The `WWW-Authenticate` values a 401 answers with — its buses' providers'."""
        return tuple(challenges_of(self.buses()))


class JsonRpcWire(Wire[RpcBinding]):
    """The JSON-RPC wire — derivation, the wire's refusals, and the served surface.

    Context: a project changes how names are derived by subclassing it (`operation_of`,
    `namespace_of`) and handing it to `RpcGateway(port=...)`; the document's title and version
    and the limits are the wire's, since what it builds carries them."""

    binding = RpcBinding

    def __init__(
        self,
        title: str = "sincpro-rpc",
        version: str = "1.0.0",
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        self.title = title
        self.version = version
        self.max_batch_size = positive_limit("max_batch_size", max_batch_size)
        self.max_body_bytes = positive_limit("max_body_bytes", max_body_bytes)

    def namespace_of(self, group: Group) -> str:
        """The group's namespace — the alias made snake_case by default — and its version."""
        namespace = group.namespace or snake_case(group.alias)
        return f"{namespace}.{group.version}" if group.version else namespace

    def operation_of(self, dto_name: str) -> str:
        return operation_name(dto_name)

    def derive(self, operation: Operation, group: Group) -> RpcBinding:
        name = f"{self.namespace_of(group)}.{self.operation_of(operation.command.__name__)}"
        return RpcBinding(name=name)

    def name_of(self, resolved: Resolved[RpcBinding]) -> str:
        return method_name_of(resolved)

    def validate(self, surface: Sequence[Resolved[RpcBinding]]) -> list[str]:
        """1. Every name is `namespace.operation`, dotted snake_case (the `rpc.` prefix is
           refused by the core, for every binding).
        2. Final: no two operations answer one name."""
        problems: list[str] = []
        by_name: dict[str, list[str]] = {}
        for one in surface:
            command = one.operation.command.__name__
            name = one.binding.name
            if name is None or not NAME_PATTERN.match(name):
                problems.append(
                    f"{command} (rpc): {name!r} is not a namespace.operation name — dotted "
                    "snake_case segments, e.g. billing.issue_invoice"
                )
                continue
            by_name.setdefault(name, []).append(f"{one.operation.alias}:{command}")
        for name, commands in by_name.items():
            if len(commands) > 1:
                problems.append(
                    f"rpc: {name} is answered by {', '.join(commands)} — name one with "
                    "@rpc(name=...) or override(..., name=...)"
                )
        return problems

    def build(self, surface: Sequence[Resolved[RpcBinding]]) -> RpcSurface:
        return RpcSurface(
            surface,
            title=self.title,
            version=self.version,
            max_batch_size=self.max_batch_size,
            max_body_bytes=self.max_body_bytes,
        )


type Reply = dict[str, Any] | list[Any] | None


def dispatch(
    surface: RpcSurface | Sequence[Resolved[RpcBinding]],
    payload: Any,
    *,
    credentials: Credentials | None = None,
    headers: Mapping[str, str] | None = None,
    context: Mapping[str, Any] | None = None,
) -> Reply:
    """Answer one JSON-RPC body — the path the gateway's own route runs, for a route of the
    project's under any framework.

    1. A surface as `gateway.surface()` answers is built with the default limits;
       `gateway.build()` carries the gateway's.
    2. `bytes` is the raw body: over `max_body_bytes` it is refused (`BODY_TOO_LARGE`), not
       JSON it is a parse error; anything else is the parsed JSON.
    3. `headers` give the context (`X-Correlation-Id`, `traceparent`), `context` goes over it,
       the body's `context` over both; `credentials` are authenticated by each bus's
       `AccessControl`, in this thread — the one the bus runs in.
    4. Final: the reply, or `None` when nothing is answered — `http_status` says its status.
    """
    served = surface if isinstance(surface, RpcSurface) else RpcSurface(surface)
    if isinstance(payload, bytes | bytearray):
        if len(payload) > served.max_body_bytes:
            return body_too_large(served.max_body_bytes)
        try:
            payload = json.loads(bytes(payload).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return parse_error("the body is not JSON")
    inherited = {**merge_http_context(headers or {}), **(context or {})}
    return handle_payload(
        served.methods,
        served.discover,
        payload,
        inherited or None,
        credentials,
        served.max_batch_size,
    )


def http_status(
    surface: RpcSurface | Sequence[Resolved[RpcBinding]], reply: Reply
) -> tuple[int, dict[str, str]]:
    """The HTTP status and headers a reply is sent with (PRD_15 §4): 204 for nothing, 413 /
    415 for a limit, 401 + `WWW-Authenticate` for a lone unauthenticated request, else 200 —
    a JSON-RPC error is still a 200."""
    if reply is None:
        return 204, {}
    if not isinstance(reply, dict):
        return 200, {}
    error = reply.get("error") or {}
    data = error.get("data") or {}
    status = STATUS_BY_REASON.get(data.get("reason", "")) if isinstance(data, dict) else None
    if status is not None:
        return status, {}
    if error.get("code") == UNAUTHENTICATED:
        served = surface if isinstance(surface, RpcSurface) else RpcSurface(surface)
        challenges = served.challenges
        return 401, {"www-authenticate": ", ".join(challenges)} if challenges else {}
    return 200, {}
