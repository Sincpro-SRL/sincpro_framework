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

    def publish(self, message: Any, *args: Any, **kwargs: Any) -> Awaitable[Any]: ...

    def subscriber(self, *args: Any, **kwargs: Any) -> Callable[..., Any]: ...

    def start(self) -> Coroutine[Any, Any, Any]: ...

    def stop(self) -> Coroutine[Any, Any, Any]: ...


class Delivery(Protocol):
    """One message as a broker handed it over, and how it is settled."""

    @property
    def body(self) -> Any: ...

    @property
    def headers(self) -> Mapping[str, Any] | None: ...

    @property
    def message_id(self) -> Any: ...

    @property
    def raw_message(self) -> Any: ...

    async def ack(self) -> Any: ...

    async def nack(self) -> Any: ...

    async def reject(self) -> Any: ...
