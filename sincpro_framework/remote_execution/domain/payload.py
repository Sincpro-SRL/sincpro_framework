"""What a call to a context hosted elsewhere travels as, and how it is read back.

    {"type": "billing.CommandIssueInvoice", "data": {...},
     "correlation_id": "01a9…", "causation_id": "01a8…", "context": {...}}

`type` is the DTO's identity (`registered_name`): the receiving bus finds its class by it. `data`
is written and read with `common.serialization`.
"""

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

from sincpro_framework.common.serialization import read_values, values_of
from sincpro_framework.sincpro_logger import logger

CORRELATION_ID = "correlation_id"
CAUSATION_ID = "causation_id"


def _travelling(context: Mapping[str, Any]) -> dict[str, Any]:
    """Each context value that JSON can write — one that cannot (a lock, a connection) stays
    here, and said."""
    written: dict[str, Any] = {}
    for key, value in context.items():
        try:
            written[key] = values_of(value)
        except Exception as error:
            logger.warning(f"context key {key!r} cannot travel and stays here: {error}")
    return written


@dataclasses.dataclass(frozen=True)
class Payload:
    """One call or one answer, as it leaves the process and as it is read back.

    Context: `correlation_id` and `causation_id` are lifted out of the context that carries
    them, so a reader of the bare document — a dead-letter queue, a log line — sees the flow.
    """

    type: str
    data: Any = None
    correlation_id: str | None = None
    causation_id: str | None = None
    context: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def of(
        cls, identity: str, value: Any, context: Mapping[str, Any] | None = None
    ) -> "Payload":
        given = dict(context or {})
        return cls(
            type=identity,
            data=value,
            correlation_id=given.pop(CORRELATION_ID, None),
            causation_id=given.pop(CAUSATION_ID, None),
            context=given,
        )

    def as_json(self) -> bytes:
        written: dict[str, Any] = {"type": self.type, "data": values_of(self.data)}
        if self.correlation_id:
            written[CORRELATION_ID] = self.correlation_id
        if self.causation_id:
            written[CAUSATION_ID] = self.causation_id
        if self.context:
            written["context"] = _travelling(self.context)
        return json.dumps(written, separators=(",", ":")).encode()

    @classmethod
    def from_json(cls, raw: bytes | str | Mapping[str, Any]) -> "Payload":
        """Read by name: what the document does not say is left at its default, what this
        process does not know is ignored. `data` stays values until a class rebuilds it."""
        read = read_values(raw)
        if not isinstance(read, Mapping):
            raise ValueError("a payload is a JSON object with its type and its data")
        context = read.get("context")
        return cls(
            type=str(read.get("type") or ""),
            data=read.get("data"),
            correlation_id=read.get(CORRELATION_ID),
            causation_id=read.get(CAUSATION_ID),
            context=dict(context) if isinstance(context, Mapping) else {},
        )

    def full_context(self) -> dict[str, Any]:
        """The context as the sender had it, its flow ids back in it."""
        flow = {CORRELATION_ID: self.correlation_id, CAUSATION_ID: self.causation_id}
        return {**self.context, **{key: one for key, one in flow.items() if one}}
