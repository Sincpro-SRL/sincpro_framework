"""Events over FastStream — Kafka, RabbitMQ, Redis, NATS — behind the `[faststream]` extra.

    queue = FastStreamQueue(broker).start()                  # the sending side: a Queue
    subscribe(broker, Subscriber(planning, assurance))       # the listening side: the buses

The domain keeps its two verbs, `publish(event)` and registering an event on a bus; which
broker carries it, and how, is this adapter's and the broker's configuration. The broker's own
driver is the service's too: `faststream[kafka]`, `faststream[rabbit]`, `faststream[redis]` or
`faststream[nats]`.
"""

FASTSTREAM_MISSING = (
    "FastStream is not installed. Install with: pip install sincpro-framework[faststream] "
    "and the broker's driver, e.g. faststream[kafka]"
)

try:
    import faststream as _faststream  # noqa: F401
except ImportError as error:  # pragma: no cover - tests/test_core_without_extras.py
    raise ImportError(FASTSTREAM_MISSING) from error


from sincpro_framework.events.faststream.consumer import event_names, subscribe
from sincpro_framework.events.faststream.queue import (
    EVENT_HEADER,
    Broker,
    FastStreamQueue,
    by_event_name,
    keyed_by_entity,
)

__all__ = [
    "EVENT_HEADER",
    "Broker",
    "FastStreamQueue",
    "by_event_name",
    "event_names",
    "keyed_by_entity",
    "subscribe",
]
