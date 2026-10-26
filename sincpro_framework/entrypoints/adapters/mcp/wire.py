"""The MCP wire of declared exposure (PRD_14 §3, §6): how a resolved surface becomes tools — each
one's name, title, description and hints — without FastMCP, which only `McpGateway.server()`
imports.

    issue_invoice    CommandIssueInvoice, `billing` — the DTO's name without its kind, snake_case
    ventas_invoice   QueryInvoice answered by two contexts: prefixed by its group, only there

Context: the gateway resolves each binding by precedence and validates what no wire can get
wrong; this wire derives what the facts prove (the name, `read_only` from a `Query`), refuses what
no MCP client could hold (a name twice, a name of more than 64 characters or outside
`[A-Za-z0-9_-]`, a Query declared destructive) and builds one `McpTool` per operation. Hints are
advice to a client, never authorization: every tool calls the bus, whose guard decides.
"""

import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from pydantic import ConfigDict

from sincpro_framework.common.naming import snake
from sincpro_framework.data_layer.caching import Idempotency
from sincpro_framework.ddd.query import Query
from sincpro_framework.entrypoints.domain.bindings import Deprecation, McpBinding
from sincpro_framework.entrypoints.domain.packed import PackedFeatureOrAppService
from sincpro_framework.entrypoints.domain.surface import Group, Operation, Resolved, Wire
from sincpro_framework.entrypoints.infrastructure.registry import entrypoint_bindings
from sincpro_framework.sincpro_abstractions import DataTransferObject

TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
"""What every MCP client accepts as a tool name: the MCP specification allows `.` and 128
characters, but the model APIs the clients hand tools to — Anthropic's, OpenAI's — accept only
this, so a longer or dotted name would build here and fail in the client."""

KIND_PREFIX = re.compile(r"^(Command|Query)(?=[A-Z0-9])")


def tool_name_of(command: type) -> str:
    """The tool name a DTO derives — its name without a leading `Command` / `Query`, snake_case
    (`CommandIssueInvoice` → `issue_invoice`), the same rule JSON-RPC names its operations by
    (`rpc.wire.operation_name`, PRD_15 §4) — derived here, since no entrypoint imports another.
    """
    return snake(KIND_PREFIX.sub("", command.__name__))


def hints_of(binding: McpBinding, idempotent: bool) -> dict[str, Any]:
    """The MCP `ToolAnnotations` of a binding — derived first, declared where it cannot be.

        hints_of(McpBinding(read_only=True), False)
        ->  {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False,
             "openWorldHint": False}
        hints_of(McpBinding(destructive=False, title="Issue"), True)
        ->  {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
             "openWorldHint": False, "title": "Issue"}

    1. `readOnlyHint` — the Command's kind: a `Query` reads.
    2. `destructiveHint` — false on a read; on a write what was declared, else the MCP
       specification's pessimistic default, true — the framework cannot prove a write harmless.
    3. `idempotentHint` — `@idempotency.once` declared: a retry is safe.
    4. `openWorldHint` — declared; false by default, a use case talks to its own context.
    5. Final: `title` only when one was declared.
    """
    read_only = binding.read_only is True
    hints: dict[str, Any] = {
        "readOnlyHint": read_only,
        "destructiveHint": False if read_only else binding.destructive is not False,
        "idempotentHint": idempotent,
        "openWorldHint": binding.open_world,
    }
    if binding.title is not None:
        hints["title"] = binding.title
    return hints


def catalog_hints(operation: PackedFeatureOrAppService) -> dict[str, Any]:
    """The hints of a catalog entry: what its facts prove, and — when its handler declares
    `@mcp` — its declared title, `destructive` and `open_world`; never its name, which stays
    the DTO's in the catalog.

        catalog_hints(<QueryInvoice>)
        ->  {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False,
             "openWorldHint": False}
    """
    handler = operation.handler or operation.dto
    declared = entrypoint_bindings.of(handler).get(McpBinding.wire)
    is_query = isinstance(operation.dto, type) and issubclass(operation.dto, Query)
    fields = (
        {}
        if declared is None
        else {name: getattr(declared, name) for name in declared.model_fields_set}
    )
    fields.pop("name", None)
    binding = McpBinding(**{"read_only": is_query, "destructive": not is_query, **fields})
    return hints_of(binding, Idempotency.declared_by(handler))


def deprecated_description(description: str, deprecation: Deprecation | None) -> str:
    """MCP has no `deprecated` field: a deprecated tool says so first in its description — the
    one thing a model reads before choosing it (PRD_14 §3).

        deprecated_description("Issue an invoice.", None)    ->  "Issue an invoice."
        deprecated_description("Issue an invoice.", Deprecation(since=date(2026, 1, 1),
            sunset=date(2026, 6, 1), replacement=CommandIssueInvoiceV2))
        ->  "DEPRECATED since 2026-01-01, removed on 2026-06-01; use issue_invoice_v2
             instead. Issue an invoice."

    Each part is said only when declared.
    """
    if deprecation is None:
        return description
    said = "DEPRECATED"
    if deprecation.since is not None:
        said += f" since {deprecation.since.isoformat()}"
    if deprecation.sunset is not None:
        said += f", removed on {deprecation.sunset.isoformat()}"
    if deprecation.replacement is not None:
        said += f"; use {tool_name_of(deprecation.replacement)} instead"
    return f"{said}. {description}"


class McpTool(DataTransferObject):
    """One tool as the wire built it — what `McpGateway.server()` registers on FastMCP, and what
    a project registers on a server of its own."""

    model_config = ConfigDict(frozen=True)

    name: str
    title: str | None
    description: str
    annotations: dict[str, Any]
    operation: Operation


class McpWire(Wire[McpBinding]):
    """Tools from a resolved surface. A project subclasses it to derive names of its own and
    hands it to `McpGateway(port=...)`.

    Context: a prefix needs the whole surface — a name is prefixed only when another context
    answers it too — so `validate` stores the names it settled for `name_of`, the manifest's.
    """

    binding = McpBinding

    def __init__(self) -> None:
        """No names settled yet — `validate` settles them for the surface it is given."""
        self._settled: dict[tuple[str, type], str] = {}

    def derive(self, operation: Operation, group: Group) -> McpBinding:
        """What the facts prove: the DTO's tool name, read-only for a Query, destructive for a
        write — the MCP default, since nothing proves a write harmless.

            derive(Operation(command=QueryInvoice, is_query=True, ...), group)
            ->  McpBinding(name="invoice", read_only=True, destructive=False)
        """
        return McpBinding(
            name=tool_name_of(operation.command),
            read_only=operation.is_query,
            destructive=not operation.is_query,
        )

    def _is_derived(self, resolved: Resolved[McpBinding]) -> bool:
        """Whether its name is the derived one — only such a name may be prefixed on a
        clash."""
        return resolved.binding.name == self.derive(resolved.operation, resolved.group).name

    def names(self, surface: Sequence[Resolved[McpBinding]]) -> list[str]:
        """Each operation's tool name, in the surface's order — its binding's, or, when a derived
        name is answered by more than one operation, `{group prefix or alias}_{name}`. A name
        that was declared is the author's: it is never renamed, a clash on it is refused."""
        counted = Counter(one.binding.name for one in surface)
        return [
            (
                f"{one.group.prefix or one.group.alias}_{one.binding.name}"
                if counted[one.binding.name] > 1 and self._is_derived(one)
                else str(one.binding.name)
            )
            for one in surface
        ]

    def name_of(self, resolved: Resolved[McpBinding]) -> str:
        """The tool name `validate` settled for it — its binding's name when none was settled
        (a surface not validated yet)."""
        operation = resolved.operation
        return self._settled.get(
            (operation.alias, operation.command), str(resolved.binding.name)
        )

    def validate(self, surface: Sequence[Resolved[McpBinding]]) -> list[str]:
        """Every reason the surface cannot be served as MCP tools — empty when it can.

            validate([<QueryInvoice in billing>, <QueryInvoice in ventas>])
            ->  []  — both derived, settled as "billing_invoice" and "ventas_invoice"
            validate([<@mcp(name="issue") in billing>, <@mcp(name="issue") in ventas>])
            ->  ["tool issue answered by CommandIssue (billing), CommandIssue (ventas) ..."]

        1. A Query declared destructive — a read destroys nothing.
        2. A name no MCP client accepts.
        3. One name answered by two operations, after prefixing.
        4. Final: the names settled, for `name_of`."""
        problems: list[str] = []
        names = self.names(surface)
        answering: dict[str, list[str]] = {}
        for one, name in zip(surface, names, strict=True):
            where = f"{one.operation.command.__name__} (mcp)"
            if one.operation.is_query and one.binding.destructive is True:
                problems.append(f"{where}: destructive=True on a Query — a Query only reads")
            if not TOOL_NAME.fullmatch(name):
                problems.append(
                    f"{where}: tool name {name!r} — an MCP tool name is 1 to 64 characters of "
                    "A-Z, a-z, 0-9, _ and -"
                )
            answering.setdefault(name, []).append(
                f"{one.operation.command.__name__} ({one.operation.alias})"
            )
        for name, commands in answering.items():
            if len(commands) > 1:
                problems.append(
                    f"tool {name} answered by {', '.join(commands)} — name one with "
                    "@mcp(name=...) or override(Command, name=...)"
                )
        self._settled = {
            (one.operation.alias, one.operation.command): name
            for one, name in zip(surface, names, strict=True)
        }
        return problems

    def build(self, surface: Sequence[Resolved[McpBinding]]) -> list[McpTool]:
        """One `McpTool` per operation, in the surface's order — its settled name, the
        declared title, the description with any deprecation said first, and its hints.

            build([<CommandIssueInvoice in billing>])
            ->  [McpTool(name="issue_invoice", title=None, description="Issue an invoice.",
                         annotations={"readOnlyHint": False, "destructiveHint": True,
                                      "idempotentHint": False, "openWorldHint": False}, ...)]
        """
        return [
            McpTool(
                name=name,
                title=one.binding.title,
                description=deprecated_description(
                    one.operation.description, one.binding.deprecated
                ),
                annotations=hints_of(one.binding, one.operation.idempotent),
                operation=one.operation,
            )
            for one, name in zip(surface, self.names(surface), strict=True)
        ]
