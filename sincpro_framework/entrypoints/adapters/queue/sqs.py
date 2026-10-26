"""`SqsBroker`: Amazon SQS as a `Broker` — the queue wire consumes it as it consumes Kafka or
RabbitMQ: envelope, producers, inbox, retries, dead letters, identity, context, the same path.

    broker = SqsBroker()                                          # boto3's default client
    QueueGateway(broker, [billing]).build()                       # a channel is a queue's name
    await broker.start()                                          # long polling until stop()

    SqsBroker(client, wait=timedelta(seconds=20), batch=10)       # what the defaults are
    SqsBroker(queues={"billing.invoices.issue": "https://sqs…/billing-issue"})

A channel is read from the queue it names: `billing.invoice.v1.issued` from
`billing-invoice-v1-issued`, as SQS takes only letters, digits, `-` and `_` (and `.fifo`) — or from
the queue `queues=` names for it, by name or URL.

Context: SQS has no consumer group — every consumer of a queue competes for its messages — and
counts every receive (`ApproximateReceiveCount`). A message is acked by deleting it; a retry makes
it visible again at once; a dead letter is left to the queue's redrive policy, so give it one whose
`maxReceiveCount` is the wire's `max_attempts`. A message that an SNS subscription or an
EventBridge rule put on the queue is opened: its body is the event, its envelope the attributes —
raw delivery or not. What runs a delivery is the wire's; this is only how SQS is read and settled.
"""

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from sincpro_framework.common.aws import aws_client
from sincpro_framework.entrypoints.adapters.queue.flavours import (
    DeadLetterBy,
    Flavour,
    RetryBy,
)
from sincpro_framework.event_driven import EVENT_HEADER, HEADERS_ATTRIBUTE
from sincpro_framework.sincpro_logger import logger


def queue_name(channel: str) -> str:
    """The SQS queue a channel is read from: every character SQS does not take becomes `-`, a
    `.fifo` ending stays."""
    fifo = channel.endswith(".fifo")
    stem = channel.removesuffix(".fifo") if fifo else channel
    return re.sub(r"[^A-Za-z0-9_-]", "-", stem) + (".fifo" if fifo else "")


SQS = Flavour(
    name="sqs",
    retry_by=RetryBy.NACK,
    dead_letter_by=DeadLetterBy.NATIVE,
    faststream=False,
)


def _opened(body: str, attributes: dict[str, str]) -> tuple[str, dict[str, str]]:
    """The event and its headers, out of what SNS or EventBridge wrapped them in.

    1. An SNS notification (raw delivery off): the body is its `Message`, the attributes its own.
    2. An EventBridge event: the body is its detail's `data`, the headers its `metadata` — or the
       whole detail, named by its detail type, when another producer put it there.
    Final: the envelope written as one attribute is spread into the headers.
    """
    try:
        wrapped = json.loads(body)
    except ValueError:
        wrapped = None
    if isinstance(wrapped, dict) and wrapped.get("Type") == "Notification":
        body = wrapped.get("Message", "")
        attributes = {
            name: one.get("Value", "")
            for name, one in (wrapped.get("MessageAttributes") or {}).items()
        }
    elif isinstance(wrapped, dict) and "detail-type" in wrapped and "detail" in wrapped:
        detail = wrapped["detail"]
        if isinstance(detail, dict) and {"metadata", "data"} <= set(detail):
            body, attributes = json.dumps(detail["data"]), dict(detail["metadata"])
        else:
            body, attributes = json.dumps(detail), {EVENT_HEADER: wrapped["detail-type"]}
    headers = dict(attributes)
    envelope = headers.pop(HEADERS_ATTRIBUTE, None)
    if envelope:
        headers = {**json.loads(envelope), **headers}
    return body, headers


class SqsDelivery:
    """One SQS message as the wire reads it — a `Delivery`."""

    def __init__(self, broker: "SqsBroker", queue_url: str, message: dict[str, Any]) -> None:
        """Open a message as `receive_message` answers it into what the wire reads.

            SqsDelivery(broker, url, {"MessageId": "m-1", "Body": '{"total": 10}',
                "Attributes": {"ApproximateReceiveCount": "2"}, "MessageAttributes":
                {"sincpro-event": {"StringValue": "billing.InvoiceIssued"}}, ...})
            ->  body b'{"total": 10}', headers {"sincpro-event": "billing.InvoiceIssued"},
                message_id "m-1", delivery_count 2

        1. Its message attributes, by name, as strings.
        2. Body and headers out of an SNS or EventBridge wrapper, the envelope spread
           (`_opened`).
        3. Final: `delivery_count` is SQS's `ApproximateReceiveCount` — `None` when the
           receive did not ask for it.
        """
        attributes = {
            name: one.get("StringValue", "")
            for name, one in (message.get("MessageAttributes") or {}).items()
        }
        body, headers = _opened(message.get("Body", ""), attributes)
        self.broker = broker
        self.queue_url = queue_url
        self.raw_message = message
        self.body = body.encode()
        self.headers = headers
        self.message_id = message.get("MessageId")
        received = (message.get("Attributes") or {}).get("ApproximateReceiveCount")
        self.delivery_count = int(received) if received else None

    async def _call(self, operation: str, **arguments: Any) -> None:
        """One call of the boto3 client on this message — its queue and receipt handle — in a
        thread, since boto3 blocks."""
        await asyncio.to_thread(
            getattr(self.broker.client, operation),
            QueueUrl=self.queue_url,
            ReceiptHandle=self.raw_message["ReceiptHandle"],
            **arguments,
        )

    async def ack(self) -> None:
        """Settled: the message is deleted from its queue."""
        await self._call("delete_message")

    async def nack(self) -> None:
        """A retry: visible again at once (`VisibilityTimeout=0`), one more receive
        counted."""
        await self._call("change_message_visibility", VisibilityTimeout=0)

    async def reject(self) -> None:
        """Visible again: the queue's redrive policy moves it once its receives run out."""
        await self._call("change_message_visibility", VisibilityTimeout=0)


class SqsBroker:
    """Amazon SQS behind the shape of a FastStream broker — `subscriber`, `publish`, `start`,
    `stop` — so `QueueGateway` subscribes and settles on it as on Kafka or RabbitMQ.

        broker = SqsBroker(queues={"billing.invoices.issue": "billing-issue"})
        QueueGateway(broker, [billing]).build()
        broker.queues_read                      ->  ["billing-issue", ...]   what to create
        await broker.start()                    # one polling task per subscribed channel

    Context: its flavour says a retry is a nack — the message made visible again, SQS counts
    the receive — and a dead letter is native: the queue's redrive policy, not the wire,
    moves it.
    """

    flavour = SQS

    def __init__(
        self,
        client: Any = None,
        wait: timedelta = timedelta(seconds=20),
        batch: int = 10,
        queues: Mapping[str, str] | Callable[[str], str] | None = None,
    ) -> None:
        """`client` is a boto3 SQS client — boto3's default when none is given; `wait` how long
        one receive waits for messages (long polling, at most 20 seconds); `batch` how many
        messages one receive takes (at most 10); `queues` the queue — name or URL — of a channel
        that is not read from its own (`queue_name`).

            SqsBroker()                      ->  boto3's default client, 20 s waits, 10 a time
            SqsBroker(sqs_client, wait=timedelta(seconds=5), batch=1)
            ->  each receive waits up to 5 s and takes one message

        Nothing is subscribed or polled yet: `subscriber` adds, `start` polls.
        """
        self.client = aws_client("sqs", client)
        self.queues = queues
        self.wait = wait
        self.batch = batch
        self._subscriptions: list[tuple[str, Callable[..., Any], int]] = []
        self._running = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    def queue_of(self, channel: str) -> str:
        """The queue — its name or URL — a channel is read from.

            queue_of("billing.invoice.v1.issued")            ->  "billing-invoice-v1-issued"
            queue_of("orders.fifo")                          ->  "orders.fifo"
            <queues={"billing.issue": "billing-issue"}> queue_of("billing.issue")
            ->  "billing-issue"
            queue_of("https://sqs.us-east-1.amazonaws.com/123/x")   ->  the URL as it is

        `queues=` first — a mapping by channel, or a function — then a URL, else
        `queue_name`.
        """
        if isinstance(self.queues, Mapping) and channel in self.queues:
            return self.queues[channel]
        if callable(self.queues):
            return self.queues(channel)
        return channel if channel.startswith(("https://", "http://")) else queue_name(channel)

    @property
    def queues_read(self) -> list[str]:
        """The queue — name or URL — of every subscribed channel: what the infrastructure has to
        create before the broker starts."""
        return list(dict.fromkeys(self.queue_of(one[0]) for one in self._subscriptions))

    def queue_url(self, channel: str) -> str:
        """The URL every SQS call on a channel needs — `queue_of` itself when it is one, else
        asked of SQS by the queue's name (`get_queue_url`, one call per use)."""
        queue = self.queue_of(channel)
        if queue.startswith(("https://", "http://")):
            return queue
        return self.client.get_queue_url(QueueName=queue)["QueueUrl"]

    def subscriber(
        self, channel: str, max_workers: int | None = None, **_: Any
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Run the handler on every message of `channel`, `max_workers` at a time (one by
        default). What a FastStream broker takes besides — a description — is ignored.

            @broker.subscriber("billing.invoice.v1.issued", max_workers=4)
            async def handle(delivery: SqsDelivery) -> None: ...
            broker.queues_read                      ->  ["billing-invoice-v1-issued"]

        Held until `start`; the handler is handed back unchanged.
        """

        def subscribed(handler: Callable[..., Any]) -> Callable[..., Any]:
            """Register the handler for `start` to poll, and hand it back unchanged."""
            self._subscriptions.append((channel, handler, max_workers or 1))
            return handler

        return subscribed

    async def publish(
        self, message: Any, channel: str, headers: dict[str, str] | None = None, **_: Any
    ) -> None:
        """A message onto `channel`, its headers as one attribute — what a counted retry or a
        dead letter by channel republishes."""
        body = message.decode() if isinstance(message, bytes | bytearray) else str(message)
        attributes = {
            HEADERS_ATTRIBUTE: {
                "DataType": "String",
                "StringValue": json.dumps(headers or {}),
            }
        }
        await asyncio.to_thread(
            self.client.send_message,
            QueueUrl=self.queue_url(channel),
            MessageBody=body,
            MessageAttributes=attributes,
        )

    async def _receive(self, queue_url: str) -> list[dict[str, Any]]:
        """One long-polling receive — up to `batch` messages, waiting up to `wait` — with
        every message attribute and the receive count the wire's attempts are read from."""
        answer = await asyncio.to_thread(
            self.client.receive_message,
            QueueUrl=queue_url,
            MaxNumberOfMessages=self.batch,
            WaitTimeSeconds=int(self.wait.total_seconds()),
            MessageAttributeNames=["All"],
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        return answer.get("Messages") or []

    async def _poll(self, channel: str, handler: Callable[..., Any], workers: int) -> int:
        """One receive on `channel`, every message handled, `workers` at a time."""
        queue_url = self.queue_url(channel)
        messages = await self._receive(queue_url)
        limit = asyncio.Semaphore(workers)

        async def one(message: dict[str, Any]) -> None:
            """Hand one message to the handler once a worker slot is free."""
            async with limit:
                await handler(SqsDelivery(self, queue_url, message))

        await asyncio.gather(*(one(message) for message in messages))
        return len(messages)

    async def poll_once(self) -> int:
        """One receive on every subscribed queue, every message handled — how many were. What
        `start` repeats; a test or a worker loop may call it by hand."""
        handled = await asyncio.gather(
            *(self._poll(*subscription) for subscription in self._subscriptions)
        )
        return sum(handled)

    async def _loop(self, channel: str, handler: Callable[..., Any], workers: int) -> None:
        """Poll one channel until `stop`. Context: a receive that fails — a throttle, the
        network — is logged and retried a second later, so one bad moment never ends the
        consumer; cancelling is the only way out."""
        while self._running.is_set():
            try:
                await self._poll(channel, handler, workers)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(f"sqs: a receive on {channel} failed; polling goes on")
                await asyncio.sleep(1)

    async def start(self) -> None:
        """Poll every subscribed queue, each on its own, until `stop`."""
        self._running.set()
        self._tasks = [
            asyncio.create_task(self._loop(*subscription))
            for subscription in self._subscriptions
        ]

    async def stop(self) -> None:
        """Stop polling: every loop is cancelled and awaited — a receive or a handler in
        progress is cancelled with it, and its message is delivered again once its visibility
        timeout passes.

            await broker.start(); ...; await broker.stop()   ->  None, no task left
            await SqsBroker().stop()                          ->  None, nothing to cancel

        A loop that ends with an error is not raised here — `return_exceptions=True`.
        """
        self._running.clear()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
