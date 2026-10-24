"""The envelope of every event an execution announces.

An execution event says what happened to one use case: which it was, what it was handed, where it
ran and in which flow. `ExecutionStarted` (incomes) says it began; `ExecutionCompleted` and
`ExecutionFailed` (outcomes) say how it ended.

They are `DomainEvent`s, so a bus or a queue receives them like any event. They are facts about an
execution and not facts of the domain: they are announced to listeners and written nowhere.

Context: this module loads the DDD layer, so nothing imports it until someone hears an event — a
process nobody listens in never loads it.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Self

from sincpro_framework.common.naming import registered_name
from sincpro_framework.context.domain.execution import Execution
from sincpro_framework.context.domain.keys import travelling
from sincpro_framework.context.domain.node import ContextNode
from sincpro_framework.context.infrastructure.tree import current
from sincpro_framework.ddd.events import DomainEvent

DESCRIBED_KEYS = ("execution_id", "causation_id")
"""What the flow carries that an event says in fields of its own, not under `context`."""


def as_json_values(value: Any) -> Any:
    """A DTO, or what a handler returned, as JSON values — described when it cannot be
    written.

        as_json_values(7)                            ->  7
        as_json_values(CommandReserve(sku="A1"))     ->  {"sku": "A1"}
        as_json_values({"at": datetime(2026, 1, 1)}) ->  {"repr": "{'at': datetime...}"}

    A pydantic model by `model_dump(mode="json")`, one with `as_json` through its JSON,
    anything else through `json.dumps` — and what none of them can write, by its `repr`.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "as_json"):
            return json.loads(value.as_json())
        return json.loads(json.dumps(value))
    except (
        Exception
    ):  # noqa: BLE001 - a value that cannot be written is described, not raised
        return {"repr": repr(value)}


def carried_context(node: ContextNode | None = None) -> dict[str, Any]:
    """What the flow carries at `node` — the node in play when none is named — the keys that
    travel, a `Secret` as its value."""
    return {
        key: value
        for key, value in travelling((node or current()).flattened()).items()
        if key not in DESCRIBED_KEYS
    }


@dataclass(kw_only=True)
class ExecutionEvent(DomainEvent):
    """What every announcement of an execution says."""

    use_case: str = ""
    """The DTO that was executed, by its identity — `billing.CommandIssueInvoice`."""
    bus: str = ""
    """The bounded context it ran in."""
    level: str = ""
    """`feature` or `application_service`."""
    dto: dict[str, Any] = field(default_factory=dict)
    """The DTO as it was handed, as JSON values."""
    execution_id: str = ""
    """The execution it is about: the same one in what it announces when it begins and when it ends."""
    context: dict[str, Any] = field(default_factory=dict)
    """What the flow carried — the keys that travel, a `Secret` as its value."""

    @classmethod
    def of_execution(
        cls,
        dto: Any,
        bus: str,
        level: str,
        execution: Execution | None,
        node: ContextNode | None = None,
        **says: Any,
    ) -> Self:
        """The event of `execution`, which handled `dto` on `bus`: its identity, the DTO as JSON
        values and what the flow carried at `node` — where it ran, the node in play when none is
        named — plus what only this kind of event says."""
        execution_id = execution.execution_id if execution is not None else ""
        return cls(
            use_case=registered_name(type(dto), bus),
            bus=bus,
            level=level,
            dto=as_json_values(dto),
            execution_id=execution_id,
            causation_id=execution_id or None,
            correlation_id=execution.correlation_id if execution is not None else None,
            entity_type="Execution",
            entity_id=execution_id,
            context=carried_context(node),
            **says,
        )
