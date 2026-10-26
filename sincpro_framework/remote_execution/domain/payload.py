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
from sincpro_framework.context import ContextKey
from sincpro_framework.sincpro_logger import logger


def _json_writable(context: Mapping[str, Any]) -> dict[str, Any]:
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
        """A call or an answer about to leave: `identity` is the registered name of the DTO's
        class, `value` the DTO itself, and the correlation and causation lifted out of
        `context` to the top.

            Payload.of("billing.CommandPay", dto, {"correlation_id": "c1", "tenant": "a"})
                ->  Payload(type="billing.CommandPay", data=dto, correlation_id="c1",
                            context={"tenant": "a"})
        """
        given = dict(context or {})
        return cls(
            type=identity,
            data=value,
            correlation_id=given.pop(ContextKey.CORRELATION_ID, None),
            causation_id=given.pop(ContextKey.CAUSATION_ID, None),
            context=given,
        )

    def as_json(self) -> bytes:
        """The compact JSON document that travels — only what it holds is written.

            Payload.of("billing.CommandPay", CommandPay(amount=10), {"correlation_id": "c1"})
            .as_json()
            ->  b'{"type":"billing.CommandPay","data":{"amount":10},"correlation_id":"c1"}'

        1. `type` and `data`, the DTO as JSON values (`values_of`).
        2. The correlation and the causation, at the top, when there are.
        3. The context, when there is — each value JSON cannot write stays in this process,
           with a warning, instead of failing the call.
        Final: UTF-8 bytes, no spaces.
        """
        written: dict[str, Any] = {"type": self.type, "data": values_of(self.data)}
        if self.correlation_id:
            written[ContextKey.CORRELATION_ID] = self.correlation_id
        if self.causation_id:
            written[ContextKey.CAUSATION_ID] = self.causation_id
        if self.context:
            written["context"] = _json_writable(self.context)
        return json.dumps(written, separators=(",", ":")).encode()

    @classmethod
    def from_json(cls, raw: bytes | str | Mapping[str, Any]) -> "Payload":
        """Read by name: what the document does not say is left at its default, what this
        process does not know is ignored. `data` stays values until a class rebuilds it.

            Payload.from_json(b'{"type":"billing.CommandPay","data":{"amount":10}}')
            ->  Payload(type="billing.CommandPay", data={"amount": 10}, context={})
            Payload.from_json(b"[1, 2]")
            ->  ValueError: a payload is a JSON object with its type and its data

        A `context` that is not an object is read as none.
        """
        read = read_values(raw)
        if not isinstance(read, Mapping):
            raise ValueError("a payload is a JSON object with its type and its data")
        context = read.get("context")
        return cls(
            type=str(read.get("type") or ""),
            data=read.get("data"),
            correlation_id=read.get(ContextKey.CORRELATION_ID),
            causation_id=read.get(ContextKey.CAUSATION_ID),
            context=dict(context) if isinstance(context, Mapping) else {},
        )

    def full_context(self) -> dict[str, Any]:
        """The context as the sender had it, its flow ids back in it."""
        flow = {
            ContextKey.CORRELATION_ID: self.correlation_id,
            ContextKey.CAUSATION_ID: self.causation_id,
        }
        return {**self.context, **{key: one for key, one in flow.items() if one}}
