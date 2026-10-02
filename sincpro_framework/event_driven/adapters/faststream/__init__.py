"""Events over FastStream — Kafka, RabbitMQ, Redis, NATS — the sending side, behind the
`[faststream]` extra and refused at import without it.

    from sincpro_framework.event_driven.adapters.faststream import FastStreamQueue, keyed_by_entity

    queue = FastStreamQueue(broker).start()       # a Queue: Publisher(queue).publish(event)

The listening side is an entrypoint — what outsiders make this process do — and lives with the
queue entrypoint: `from sincpro_framework.entrypoints.faststream import subscribe`. The broker's
own driver is the service's: `faststream[kafka]`, `faststream[rabbit]`, `faststream[redis]` or
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

from sincpro_framework.event_driven.adapters.faststream.queue import (  # noqa: E402 - after the guard
    EVENT_HEADER,
    Broker,
    FastStreamQueue,
    by_event_name,
    keyed_by_entity,
)

__all__ = [
    "EVENT_HEADER",
    "FASTSTREAM_MISSING",
    "Broker",
    "FastStreamQueue",
    "by_event_name",
    "keyed_by_entity",
]
