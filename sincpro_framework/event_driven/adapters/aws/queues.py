"""`SnsQueue`, `SqsQueue`, `EventBridgeQueue`: a `Queue` that publishes an event to Amazon SNS,
SQS or EventBridge — what `Publisher(queue)` and `bus.publish_*(to=...)` hand events to.

    Publisher(SnsQueue("arn:aws:sns:us-east-1:123:billing")).publish(InvoicePaid(...))
    billing.publish_failures(to=Publisher(SqsQueue(queue_url)), use_cases=[CommandPay])
    Publisher(EventBridgeQueue("billing-bus", source="billing")).publish(InvoicePaid(...))

Context: the body is the event as JSON; its envelope — id, wire name, chain, trace, context — goes
beside it (`attributes`), so the queue wire on the other side reads it as from any broker. A FIFO
topic or queue delivers one entity's events in order and delivers a retried event once. On
EventBridge, whose rules read the body, the detail is `{"metadata": <envelope>, "data": <event>}`
and the detail type is the event's wire name. What the service does not take is a
`ServiceUnavailableError`: retry later. Sending waits for AWS to take it; `aput` does it on a
worker thread.
"""

import asyncio
import json
from typing import Any

from sincpro_framework import exceptions
from sincpro_framework.common.aws import aws_client
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven.adapters.aws.attributes import (
    AttributesOf,
    fifo_fields,
    filtered_on,
    message_attributes,
)
from sincpro_framework.event_driven.domain.envelope import Envelope


def _sent(service: str, event: DomainEvent, send: Any) -> None:
    """Call `send`; any error from AWS becomes a `ServiceUnavailableError` naming the service
    and the event — a forwarding and a caller read it as "retry later", whatever boto raised.
    """
    try:
        send()
    except Exception as error:
        raise exceptions.ServiceUnavailableError(
            f"{service} did not take {event.name}: {type(error).__name__}: {error}"
        ) from error


class SnsQueue:
    """A `Queue` publishing each event to one SNS topic — fan-out to every subscription."""

    def __init__(
        self,
        topic_arn: str,
        client: Any = None,
        source: str = "",
        attributes_of: AttributesOf = filtered_on,
    ) -> None:
        """`topic_arn` is where every event goes; `source` names this service in the envelope;
        `attributes_of` what a subscription filters on (`filtered_on`)."""
        self.topic_arn = topic_arn
        self.client = aws_client("sns", client)
        self.source = source
        self.attributes_of = attributes_of

    def put(self, event: DomainEvent) -> None:
        """Publish `event` to the topic and wait for SNS to take it.

        1. The envelope is read here, in the publishing execution.
        2. The body is the event's JSON; the envelope and the filter fields go as message
           attributes; a `.fifo` topic adds the group (entity, else flow) and dedup id.
        Final: SNS took it — or `ServiceUnavailableError`.
        """
        headers = Envelope.headers_for(event, source=self.source)
        _sent(
            "SNS",
            event,
            lambda: self.client.publish(
                TopicArn=self.topic_arn,
                Message=event.as_json(),
                MessageAttributes=message_attributes(headers, self.attributes_of(event)),
                **fifo_fields(self.topic_arn, event),
            ),
        )

    async def aput(self, event: DomainEvent) -> None:
        """`put` on a worker thread — boto is blocking, the event loop is not held."""
        await asyncio.to_thread(self.put, event)


class SqsQueue:
    """A `Queue` sending each event to one SQS queue — one consumer group reads it."""

    def __init__(
        self,
        queue_url: str,
        client: Any = None,
        source: str = "",
        attributes_of: AttributesOf = filtered_on,
    ) -> None:
        """`queue_url` is where every event goes; `source` names this service in the envelope."""
        self.queue_url = queue_url
        self.client = aws_client("sqs", client)
        self.source = source
        self.attributes_of = attributes_of

    def put(self, event: DomainEvent) -> None:
        """Send `event` to the queue and wait for SQS to take it — the same body, attributes
        and FIFO fields as `SnsQueue.put`."""
        headers = Envelope.headers_for(event, source=self.source)
        _sent(
            "SQS",
            event,
            lambda: self.client.send_message(
                QueueUrl=self.queue_url,
                MessageBody=event.as_json(),
                MessageAttributes=message_attributes(headers, self.attributes_of(event)),
                **fifo_fields(self.queue_url, event),
            ),
        )

    async def aput(self, event: DomainEvent) -> None:
        """`put` on a worker thread — boto is blocking, the event loop is not held."""
        await asyncio.to_thread(self.put, event)


class EventBridgeQueue:
    """A `Queue` putting each event on an EventBridge bus, for its rules to route."""

    def __init__(
        self, bus: str = "default", source: str = "sincpro", client: Any = None
    ) -> None:
        """`bus` is the event bus's name or ARN; `source` is the entry's `Source`, what a rule
        matches first, and this service in the envelope."""
        self.bus = bus
        self.source = source
        self.client = aws_client("events", client)

    def put(self, event: DomainEvent) -> None:
        """Put `event` on the bus as one entry and wait for EventBridge to take it.

            detail      {"metadata": {"ce_id": "…", "ce_type": "billing.InvoicePaid", …},
                         "data": {…the event…}}
            DetailType  "billing.InvoicePaid"

            EventBridgeQueue(bus="billing").put(InvoicePaid(…))    ->  None, once taken
            # FailedEntryCount 1, ErrorCode "ThrottlingException"
                ->  raises ServiceUnavailableError("EventBridge did not take
                    billing.InvoicePaid: RuntimeError: ThrottlingException: …")

        Context: `put_events` answers 200 even when it refused the entry; the refusal is in
        `FailedEntryCount`, read here and raised — so it becomes a `ServiceUnavailableError`
        like a transport error.
        """
        headers = Envelope.headers_for(event, source=self.source)
        detail = {"metadata": headers, "data": json.loads(event.as_json())}

        def send() -> None:
            answer = self.client.put_events(
                Entries=[
                    {
                        "EventBusName": self.bus,
                        "Source": self.source,
                        "DetailType": event.name,
                        "Detail": json.dumps(detail),
                    }
                ]
            )
            if answer.get("FailedEntryCount"):
                failed = answer["Entries"][0]
                raise RuntimeError(f"{failed.get('ErrorCode')}: {failed.get('ErrorMessage')}")

        _sent("EventBridge", event, send)

    async def aput(self, event: DomainEvent) -> None:
        """`put` on a worker thread — boto is blocking, the event loop is not held."""
        await asyncio.to_thread(self.put, event)
