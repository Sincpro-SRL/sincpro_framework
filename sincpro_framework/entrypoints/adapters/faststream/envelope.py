"""CloudEvents 1.0 in binary mode: the envelope rides in the protocol headers, the body stays the
payload as it is (PRD_15 §5.3).

    headers = cloud_event_headers(event, source="urn:svc:sales")    # a producer stamps them
    await broker.publish(event.as_json().encode(), "billing.invoices", headers=headers)

    envelope = Envelope.of(message.headers)                          # the gateway reads them

Context: headers are written with the `ce_` prefix — the Kafka binding's, and a plain header name
FastStream carries unchanged on Kafka, RabbitMQ, Redis and NATS. Reading also accepts `ce-` (the
HTTP binding) and `cloudEvents:` / `cloudEvents_` (the AMQP binding), so a producer that follows
its own broker's binding is understood. A message without an envelope — `FastStreamQueue` today
— still works: its type is the `sincpro-event` header, its id the event's own.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import ConfigDict

from sincpro_framework.common.naming import registered_name
from sincpro_framework.context.adapters.propagation import inject
from sincpro_framework.context.domain.execution import CAUSATION_ID, CORRELATION_ID
from sincpro_framework.context.infrastructure.tree import handed_on, live_context
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven.adapters.faststream.queue import EVENT_HEADER
from sincpro_framework.observability.tracing.propagation import trace_carrier
from sincpro_framework.sincpro_abstractions import DataTransferObject

SPEC_VERSION = "1.0"
WRITTEN_PREFIX = "ce_"
READ_PREFIXES = ("ce_", "ce-", "cloudEvents:", "cloudEvents_")
ATTEMPTS_HEADER = "sincpro-attempts"
"""How many times this message was already tried — written by the gateway when it retries by
publishing a copy, read with the broker's own delivery count."""

type Headers = Mapping[str, Any]


def text_headers(headers: Headers | None) -> dict[str, str]:
    """Every header as text — Kafka hands bytes, the others text."""
    found: dict[str, str] = {}
    for name, value in (headers or {}).items():
        if isinstance(value, bytes | bytearray):
            found[str(name)] = bytes(value).decode("utf-8", errors="replace")
        elif value is not None:
            found[str(name)] = str(value)
    return found


def _attribute(headers: Mapping[str, str], name: str) -> str | None:
    for prefix in READ_PREFIXES:
        value = headers.get(f"{prefix}{name}")
        if value:
            return value
    return None


def _attempts(headers: Mapping[str, str]) -> int:
    try:
        return max(int(headers.get(ATTEMPTS_HEADER, "1")), 1)
    except ValueError:
        return 1


class Envelope(DataTransferObject):
    """What a message says about itself: which occurrence (`id`), who produced it (`source`),
    what it is (`type`), about which entity (`subject`), when (`time`), and the chain it belongs
    to (`correlationid`, `causationid`) — every field optional, since a message of a producer
    that writes no envelope is still consumed."""

    model_config = ConfigDict(frozen=True)

    id: str | None = None
    source: str | None = None
    type: str | None = None
    subject: str | None = None
    time: datetime | None = None
    correlationid: str | None = None
    causationid: str | None = None
    traceparent: str | None = None
    tracestate: str | None = None
    attempts: int = 1
    """This delivery's attempt as the attempts header says — 1 when it says nothing."""

    @classmethod
    def of(cls, headers: Headers | None) -> "Envelope":
        """The envelope in a message's headers; its type falls back to the `sincpro-event`
        header `FastStreamQueue` writes."""
        text = text_headers(headers)
        time = _attribute(text, "time")
        try:
            parsed = datetime.fromisoformat(time) if time else None
        except ValueError:
            parsed = None
        return cls(
            id=_attribute(text, "id"),
            source=_attribute(text, "source"),
            type=_attribute(text, "type") or text.get(EVENT_HEADER),
            subject=_attribute(text, "subject"),
            time=parsed,
            correlationid=_attribute(text, "correlationid"),
            causationid=_attribute(text, "causationid"),
            traceparent=text.get("traceparent") or _attribute(text, "traceparent"),
            tracestate=text.get("tracestate") or _attribute(text, "tracestate"),
            attempts=_attempts(text),
        )

    def carrier(self) -> dict[str, str]:
        """The W3C trace headers, for `within_trace`."""
        found = {"traceparent": self.traceparent, "tracestate": self.tracestate}
        return {name: value for name, value in found.items() if value}

    def as_headers(self) -> dict[str, str]:
        """Binary mode: each attribute as a `ce_` header, the trace as W3C headers."""
        headers = {f"{WRITTEN_PREFIX}specversion": SPEC_VERSION}
        attributes = {
            "id": self.id,
            "source": self.source,
            "type": self.type,
            "subject": self.subject,
            "time": None if self.time is None else self.time.isoformat(),
            "correlationid": self.correlationid,
            "causationid": self.causationid,
        }
        for name, value in attributes.items():
            if value:
                headers[f"{WRITTEN_PREFIX}{name}"] = value
        headers.update(self.carrier())
        return headers


def cloud_event_headers(
    message: DomainEvent | DataTransferObject | None = None,
    source: str = "",
    type: str | None = None,
    id: str | None = None,
    subject: str | None = None,
    context: str = "",
) -> dict[str, str]:
    """The headers a producer publishes a message with — what the gateway checks `producers=`
    against (`source`) and keys the inbox by (`source` + `id`).

    1. A DomainEvent gives its own id, wire name, entity, time and chain.
    2. A Command gives its identity in `context` — the bus that answers it — as the type
       (`registered_name`), and a fresh id — publish the same headers
       again when retrying, so the inbox recognises the retry — and the chain of the execution
       sending it: caused by it, in its flow.
    3. Final: the running trace, the execution's context (`sincpro-context`), and the
       `sincpro-event` header for a DomainEvent, so `subscribe()` routes it as well.
    """
    if isinstance(message, DomainEvent):
        envelope = Envelope(
            id=id or message.id,
            source=source,
            type=type or message.name,
            subject=subject or message.entity_id or None,
            time=message.created_at,
            correlationid=message.correlation_id,
            causationid=message.causation_id,
        )
    else:
        chain = handed_on({})
        envelope = Envelope(
            id=id or uuid4().hex,
            source=source,
            type=type or (None if message is None else type_name(message, context)),
            subject=subject,
            correlationid=chain.get(CORRELATION_ID),
            causationid=chain.get(CAUSATION_ID),
        )
    carrier = trace_carrier()
    envelope = envelope.model_copy(
        update={
            "traceparent": carrier.get("traceparent"),
            "tracestate": carrier.get("tracestate"),
        }
    )
    headers = envelope.as_headers()
    headers = {**inject(live_context()), **headers}
    if isinstance(message, DomainEvent):
        headers[EVENT_HEADER] = message.name
    return headers


def type_name(message: Any, context: str = "") -> str:
    """The wire type of a message, its identity: a DomainEvent's `name`, a Command's
    `context.Class` (`registered_name`)."""
    cls = message if isinstance(message, type) else type(message)
    return registered_name(cls, context)
