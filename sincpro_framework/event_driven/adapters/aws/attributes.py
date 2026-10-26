"""What an event carries on an Amazon message beside its body: the envelope, and the few fields a
subscription filters on.

    sincpro-headers    the envelope and the context, as JSON — `Envelope.headers_for`
    sincpro-event      the event's wire name
    use_case · bus · level · kind · tenant_id     an execution event's, when it says them

Context: SNS and SQS carry at most ten message attributes, so the envelope travels as one of them
and the rest are what an SNS filter policy matches on — `{"use_case": ["billing.CommandPay"]}`
without reading the body. `attributes_of=` on a queue says others; `filtered_on` is the default.
"""

import json
from collections.abc import Callable
from typing import Any

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven.domain.envelope import EVENT_HEADER, HEADERS_ATTRIBUTE

LIMIT = 10
"""How many message attributes SNS and SQS take."""

type AttributesOf = Callable[[DomainEvent], dict[str, str]]


def filtered_on(event: DomainEvent) -> dict[str, str]:
    """The fields a subscription filters on: the event's name, and an execution event's use
    case, bus, level, kind of failure and tenant — the ones it says.

        filtered_on(InvoiceIssued(…))
            ->  {"sincpro-event": "billing.InvoiceIssued"}
        filtered_on(ExecutionFailed(use_case="billing.CommandPay", bus="billing",
                                    kind="unavailable", context={"tenant_id": "bo"}, …))
            ->  {"sincpro-event": "…ExecutionFailed", "use_case": "billing.CommandPay",
                 "bus": "billing", "kind": "unavailable", "tenant_id": "bo"}

    Context: only text that is not empty is said — a field missing, `None` or not text is
    left out rather than written as `"None"`.
    """
    said: dict[str, str] = {EVENT_HEADER: event.name}
    for name in ("use_case", "bus", "level", "kind"):
        value = getattr(event, name, None)
        if isinstance(value, str) and value:
            said[name] = value
    context = getattr(event, "context", None)
    tenant = context.get("tenant_id") if isinstance(context, dict) else None
    if isinstance(tenant, str) and tenant:
        said["tenant_id"] = tenant
    return said


def message_attributes(
    headers: dict[str, str], said: dict[str, str]
) -> dict[str, dict[str, str]]:
    """The attributes of one message — the envelope as one, then what a filter reads, within
    the ten a message takes."""
    written = {HEADERS_ATTRIBUTE: json.dumps(headers), **said}
    return {
        name: {"DataType": "String", "StringValue": value}
        for name, value in list(written.items())[:LIMIT]
    }


def fifo_fields(target: str, event: DomainEvent) -> dict[str, Any]:
    """On a FIFO topic or queue: the events of one entity — else of one flow — in order, and a
    retry of the same event delivered once."""
    if not target.endswith(".fifo"):
        return {}
    group = event.entity_id or event.correlation_id or event.name
    return {"MessageGroupId": group, "MessageDeduplicationId": event.id}
