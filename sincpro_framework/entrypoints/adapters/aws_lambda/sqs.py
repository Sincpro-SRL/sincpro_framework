"""`sqs_handler`: an AWS Lambda fed by SQS that runs every message through the queue wire — the
same path, the same exposure, the same settling as a process polling the queue.

    # billing/lambda_function.py
    from sincpro_framework.entrypoints.adapters.aws_lambda import sqs_handler

    handler = sqs_handler([billing])           # what the function's configuration names

Context: Lambda receives a batch and deletes it when the function returns. Turn on
`ReportBatchItemFailures` in the event source mapping: a message the wire did not ack is answered
in `batchItemFailures`, so only it comes back — retried, and moved by the queue's redrive policy
when its receives run out. A record is matched to its subscription by the queue it came from —
the queue a channel is read from (`SqsBroker.queue_of`): `billing.invoice.v1.issued` from
`billing-invoice-v1-issued`, or what `broker=SqsBroker(queues=...)` says.
"""

from collections.abc import Callable, Sequence
from typing import Any

from sincpro_framework.entrypoints.adapters.queue.gateway import QueueGateway
from sincpro_framework.entrypoints.adapters.queue.sqs import SqsBroker, SqsDelivery
from sincpro_framework.entrypoints.adapters.queue.wire import QueueOptions
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework


class _RecordDelivery(SqsDelivery):
    """A record of the batch: settled by being in the answer or not, never by SQS's API."""

    def __init__(self, broker: SqsBroker, record: dict[str, Any]) -> None:
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
        self.failed = False

    async def nack(self) -> None:
        self.failed = True

    async def reject(self) -> None:
        self.failed = True


def _queue_name(record: dict[str, Any]) -> str:
    return record.get("eventSourceARN", "").rsplit(":", 1)[-1]


def sqs_handler(
    buses: Sequence[UseFramework],
    options: QueueOptions | None = None,
    broker: SqsBroker | None = None,
) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    """The function Lambda calls: every record run through the queue wire of a `QueueGateway`
    over `buses`, the ones it did not ack answered as failures.

        in      {"Records": [three messages, the second fails]}
        out     {"batchItemFailures": [{"itemIdentifier": "<id of the second>"}]}

    `options` is the wire's (`QueueOptions`: the inbox, `max_attempts`, ...); `broker` an
    `SqsBroker` of the project's, for the client a retry by channel publishes with.
    """
    import asyncio

    sqs = broker or SqsBroker()
    gateway = QueueGateway(sqs, list(buses), options=options)
    subscriptions = {
        sqs.queue_of(one.channel).rsplit("/", 1)[-1]: one for one in gateway.build()
    }

    async def handled(records: list[dict[str, Any]]) -> list[dict[str, str]]:
        failures: list[dict[str, str]] = []
        for record in records:
            delivery = _RecordDelivery(sqs, record)
            subscription = subscriptions.get(_queue_name(record))
            if subscription is None:
                logger.error(f"lambda: no subscription for queue {_queue_name(record)!r}")
                delivery.failed = True
            else:
                await gateway.queue_wire.process(
                    delivery, subscription.by_type, subscription.channel
                )
            if delivery.failed:
                failures.append({"itemIdentifier": str(delivery.message_id)})
        return failures

    def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
        return {"batchItemFailures": asyncio.run(handled(event.get("Records") or []))}

    return handler
