"""The queue entrypoint on FastStream — Commands consumed and events heard from a broker, settled
from a verdict, behind the `[faststream]` extra (PRD_15 §5).

    gateway = QueueGateway(KafkaBroker("kafka:9092"), [billing, accounting])
    gateway.build()

Context: the events module keeps how a fact *leaves* the process (`Publisher`, `FastStreamQueue`,
`subscribe()`); this package owns what outsiders may make this process do — exposure, the
producer's identity, the inbox, settling, dead letters, CloudEvents and AsyncAPI.
"""

from sincpro_framework.entrypoints.faststream.envelope import (
    ATTEMPTS_HEADER,
    Envelope,
    cloud_event_headers,
)
from sincpro_framework.entrypoints.faststream.flavours import DeadLetterBy, RetryBy
from sincpro_framework.entrypoints.faststream.gateway import QueueGateway
from sincpro_framework.entrypoints.faststream.inbox import inbox_key
from sincpro_framework.entrypoints.faststream.verdicts import Settlement, Verdict
from sincpro_framework.entrypoints.faststream.wire import (
    DEAD_LETTER_FROM,
    DEAD_LETTER_KIND,
    DEAD_LETTER_REASON,
    QueueOptions,
    QueueWire,
    Subscription,
    by_envelope_source,
    rabbit_user_id,
)
from sincpro_framework.events.faststream import FASTSTREAM_MISSING  # noqa: F401 - the extra

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
    "Subscription",
    "Verdict",
    "by_envelope_source",
    "cloud_event_headers",
    "inbox_key",
    "rabbit_user_id",
]
