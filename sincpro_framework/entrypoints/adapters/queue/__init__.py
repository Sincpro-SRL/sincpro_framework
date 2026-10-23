"""The queue entrypoint — Commands consumed and events heard from a broker, settled from a
verdict (PRD_15 §5): a FastStream broker (`[faststream]`) or any other `Broker`, such as Amazon
SQS (`SqsBroker`, `[aws]`).

    gateway = QueueGateway(KafkaBroker("kafka:9092"), [billing, accounting])
    gateway.build()
    subscribe(broker, Subscriber(planning, assurance))    # the shortcut: every event the buses hear

Context: the event-driven module keeps how a fact *leaves* the process (`Publisher`,
`FastStreamQueue`); this package owns what outsiders may make this process do — exposure, the
producer's identity, the inbox, settling, dead letters, CloudEvents and AsyncAPI — and so where a
process listens.
"""

from sincpro_framework.entrypoints.adapters.queue.flavours import DeadLetterBy, RetryBy
from sincpro_framework.entrypoints.adapters.queue.gateway import QueueGateway
from sincpro_framework.entrypoints.adapters.queue.inbox import inbox_key
from sincpro_framework.entrypoints.adapters.queue.sqs import SqsBroker, queue_name
from sincpro_framework.entrypoints.adapters.queue.subscribe import (
    by_name,
    event_names,
    subscribe,
)
from sincpro_framework.entrypoints.adapters.queue.verdicts import Settlement, Verdict
from sincpro_framework.entrypoints.adapters.queue.wire import (
    DEAD_LETTER_FROM,
    DEAD_LETTER_KIND,
    DEAD_LETTER_REASON,
    QueueOptions,
    QueueWire,
    Subscription,
    by_envelope_source,
    rabbit_user_id,
)
from sincpro_framework.event_driven.adapters.envelope import (
    ATTEMPTS_HEADER,
    Envelope,
    cloud_event_headers,
)

__all__ = [
    "ATTEMPTS_HEADER",
    "DEAD_LETTER_FROM",
    "DEAD_LETTER_KIND",
    "DEAD_LETTER_REASON",
    "DeadLetterBy",
    "Envelope",
    "QueueGateway",
    "QueueOptions",
    "QueueWire",
    "RetryBy",
    "Settlement",
    "SqsBroker",
    "Subscription",
    "Verdict",
    "by_envelope_source",
    "by_name",
    "event_names",
    "subscribe",
    "cloud_event_headers",
    "inbox_key",
    "queue_name",
    "rabbit_user_id",
]
