"""`Broker` and `Delivery`: what the queue wire needs of a message broker and of one message it
handed over — any broker, FastStream's or one of the project's.

    broker.subscriber("billing.invoices", ...)(handle)      # handle(message) on every delivery
    await broker.publish(body, "billing.invoices", headers=headers)

    message.body · message.headers · message.message_id · message.raw_message
    await message.ack() · await message.nack() · await message.reject()

Context: a FastStream broker (Kafka, RabbitMQ, Redis, NATS) already is one, and its messages
already are deliveries. A broker of another kind — Amazon SQS, a test double — implements these
few methods and says how it settles with a `flavour` attribute; then the whole queue wire —
envelope, producers, inbox, retries, dead letters, identity, context — runs on it unchanged.
"""

from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import Any, Protocol


class Broker(Protocol):
    """What the queue wire and a broker queue use of a broker."""

    def publish(self, message: Any, *args: Any, **kwargs: Any) -> Awaitable[Any]:
        """Send `message` — the destination and the headers in the broker's own arguments."""
        ...

    def subscriber(self, *args: Any, **kwargs: Any) -> Callable[..., Any]:
        """A decorator registering a handler for the source the arguments name — called with
        each `Delivery`."""
        ...

    def start(self) -> Coroutine[Any, Any, Any]:
        """Connect and start consuming — awaited before the first message is handled."""
        ...

    def stop(self) -> Coroutine[Any, Any, Any]:
        """Stop consuming and close the connection."""
        ...


class Delivery(Protocol):
    """One message as a broker handed it over, and how it is settled."""

    @property
    def body(self) -> Any:
        """The payload as sent — bytes, or the broker's decoded value."""
        ...

    @property
    def headers(self) -> Mapping[str, Any] | None:
        """The message's headers or attributes — where identity, context, attempts travel."""
        ...

    @property
    def message_id(self) -> Any:
        """The broker's id of the message — what the inbox deduplicates by when the event's
        own id is missing."""
        ...

    @property
    def raw_message(self) -> Any:
        """The broker's own message object, for what only that broker knows."""
        ...

    async def ack(self) -> Any:
        """Settle as handled — the broker never delivers it again."""
        ...

    async def nack(self) -> Any:
        """Settle as not handled — the broker delivers it again."""
        ...

    async def reject(self) -> Any:
        """Settle as never to be handled — dropped, or dead-lettered where the broker does."""
        ...
