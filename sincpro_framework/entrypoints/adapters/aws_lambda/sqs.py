"""A Lambda fed by SQS, read as the queue wire reads a polled queue: each record of a batch a
delivery, settled by being answered in `batchItemFailures` or not.

    batch_handler(sqs, gateway.queue_wire, gateway.build())
    ->  handler({"Records": [three messages, the second fails]}, None)
        == {"batchItemFailures": [{"itemIdentifier": "<id of the second>"}]}

Context: Lambda receives a batch and deletes it when the function returns. Turn on
`ReportBatchItemFailures` in the event source mapping: a message the wire did not ack is answered
in `batchItemFailures`, so only it comes back — retried, and moved by the queue's redrive policy
when its receives run out. A record is matched to its subscription by the queue it came from —
the queue a channel is read from (`SqsBroker.queue_of`): `billing.invoice.v1.issued` from
`billing-invoice-v1-issued`, or what `broker=SqsBroker(queues=...)` says.
"""

import asyncio
from collections.abc import Callable, Iterable
from typing import Any

from sincpro_framework.entrypoints.adapters.queue.sqs import SqsBroker, SqsDelivery
from sincpro_framework.entrypoints.adapters.queue.wire import QueueWire, Subscription
from sincpro_framework.sincpro_logger import logger


class _RecordDelivery(SqsDelivery):
    """A record of the batch: settled by being in the answer or not, never by SQS's API."""

    def __init__(self, broker: SqsBroker, record: dict[str, Any]) -> None:
        """Read a Lambda record as the SQS message `receive_message` would have answered.

            {"messageId": "m-1", "body": "...", "eventSourceARN": "...:billing",
             "messageAttributes": {"sincpro-headers": {"stringValue": "{...}"}}}
            ->  SqsDelivery over {"MessageId": "m-1", "Body": "...", "MessageAttributes":
                {"sincpro-headers": {"StringValue": "{...}"}}, ...}

        Context: Lambda spells the keys in camelCase; renamed, the record is opened like a
        polled message — SNS and EventBridge wrappers, the envelope attribute, the receive
        count. It starts as not failed.
        """
        message = {
            "MessageId": record.get("messageId"),
            "ReceiptHandle": record.get("receiptHandle"),
            "Body": record.get("body", ""),
            "Attributes": record.get("attributes") or {},
            "MessageAttributes": {
                name: {"StringValue": one.get("stringValue", "")}
                for name, one in (record.get("messageAttributes") or {}).items()
            },
        }
        super().__init__(broker, record.get("eventSourceARN", ""), message)
        self.failed = False

    async def ack(self) -> None:
        """Settled: left out of `batchItemFailures`, so Lambda deletes it with the batch."""
        self.failed = False

    async def nack(self) -> None:
        """A retry: answered in `batchItemFailures`, so SQS delivers it again."""
        self.failed = True

    async def reject(self) -> None:
        """Answered in `batchItemFailures` as a retry is: the queue's redrive policy moves it
        to the dead-letter queue once its receives run out."""
        self.failed = True


def _queue_name(record: dict[str, Any]) -> str:
    """The queue a record came from — the last part of its ARN.

        _queue_name({"eventSourceARN": "arn:aws:sqs:us-east-1:123:billing-issue"})
        ->  "billing-issue"

    The wire's subscriptions are found by that name.
    """
    return record.get("eventSourceARN", "").rsplit(":", 1)[-1]


def batch_handler(
    sqs: SqsBroker, wire: QueueWire, subscriptions: Iterable[Subscription]
) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    """The function Lambda calls: every record run through `wire`, matched to one of
    `subscriptions` by the queue it came from, the ones it did not ack answered as failures.

        handler = batch_handler(sqs, gateway.queue_wire, gateway.build())
        handler({"Records": [<from billing-invoice-v1-issued, acked>]}, None)
        ->  {"batchItemFailures": []}
        handler({"Records": [<from a queue nobody reads, id "m-9">]}, None)
        ->  {"batchItemFailures": [{"itemIdentifier": "m-9"}]}

    1. Each subscription keyed by the name of the queue its channel is read from — the last
       part of `queue_of(channel)`.
    Final: `handler`, which runs each batch through the wire in an event loop of its own
    (`handled`) and answers the records it did not ack.
    """
    by_queue = {sqs.queue_of(one.channel).rsplit("/", 1)[-1]: one for one in subscriptions}

    async def handled(records: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Run the batch through the wire, one record after the other, and answer the ones
        that failed.

        1. Each record becomes a delivery, matched to its subscription by its queue's name.
        2. A queue no subscription reads is logged and failed — SQS retains it for its redrive.
        3. Otherwise the wire processes it as it processes a polled message.
        Final: `{"itemIdentifier": <message id>}` for every delivery the wire did not ack.
        """
        failures: list[dict[str, str]] = []
        for record in records:
            delivery = _RecordDelivery(sqs, record)
            subscription = by_queue.get(_queue_name(record))
            if subscription is None:
                logger.error(f"lambda: no subscription for queue {_queue_name(record)!r}")
                delivery.failed = True
            else:
                await wire.process(delivery, subscription.by_type, subscription.channel)
            if delivery.failed:
                failures.append({"itemIdentifier": str(delivery.message_id)})
        return failures

    def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
        """What Lambda calls with each batch — the wire is async, so each call runs it in an
        event loop of its own and answers the partial batch response."""
        return {"batchItemFailures": asyncio.run(handled(event.get("Records") or []))}

    return handler
