"""Where a node of the context sits, what kind of entrance opened a flow, and who set a value."""

from dataclasses import dataclass
from enum import StrEnum


class Level(StrEnum):
    """A node's place in the tree — the layers of the architecture, outermost first."""

    ROOT = "root"
    """The process: its defaults, its flags, what any execution of it may read."""
    BUS = "bus"
    """What a bounded context publishes for every execution of its own (`global_scope=True`)."""
    ENTRYPOINT = "entrypoint"
    """What the entrance knew: who, for which tenant, in which language, from which flow."""
    APPLICATION = "application"
    """One execution of an ApplicationService."""
    FEATURE = "feature"
    """One execution of a Feature."""
    HOOK = "hook"
    """One run of a repository's hook."""
    SCOPE = "scope"
    """A block that changed something for what it calls."""


EXECUTIONS = frozenset({Level.APPLICATION, Level.FEATURE})
SCOPES = frozenset({Level.ENTRYPOINT, Level.SCOPE})
"""The nodes a write to the context as a mapping lands on — the scope of a call."""


class EntrypointKind(StrEnum):
    """Which entrance opened a flow."""

    DIRECT = "direct"
    """`bus(dto)` called by code: a script, a test, a caller with no transport."""
    REST = "rest"
    RPC = "rpc"
    GRPC = "grpc"
    MCP = "mcp"
    QUEUE = "queue"
    CRON = "cron"
    REMOTE = "remote"
    """Another service running the same code (`remote_execution`)."""


@dataclass(frozen=True)
class Origin:
    """The node a value came from."""

    level: Level
    label: str
    """The bus, the use case or the hook — what the node is named after."""
    execution_id: str | None
    """The execution it belongs to; `None` above every execution."""
