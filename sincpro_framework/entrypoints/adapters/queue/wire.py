"""`QueueWire`: the queue wire on a broker — what it derives, what it refuses, the subscriptions
it registers, and the one path every delivery takes (PRD_15 §1.2, §5).

    envelope → producer allowed? → expired? → decode → inbox claim → bus(dto) → verdict → settle
                                                         └ identity, access guard, idempotency,
                                                           caching, interceptors — the bus's own

Context: the generated subscriber and `QueueGateway.consume` (full control) both call `process`,
so a hand-written subscriber can neither skip nor re-implement a stage. Nothing here is a broker's:
a FastStream broker (Kafka, RabbitMQ, Redis, NATS) and one of another kind (Amazon SQS) are both a
`Broker`, their messages a `Delivery` (`sincpro_framework.event_driven`). Every subscription
settles manually from the verdict; the bus runs in a worker thread, inside the producer's trace,
authenticated in that same thread (identity is a `ContextVar`).
"""

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sincpro_framework import exceptions
from sincpro_framework.auth import Credentials, security
from sincpro_framework.common.failures import failure_reason, refined_failure_kind
from sincpro_framework.common.naming import registered_name
from sincpro_framework.context import ContextKey, EntrypointKind, context_propagation
from sincpro_framework.data_layer.caching import IdempotencyRecords, KeyValueRecords
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.entrypoints.adapters.queue.flavours import (
    BrokerName,
    DeadLetterBy,
    RetryBy,
    SubscriptionOf,
    delivery_count,
    flavour_of,
    republish_options,
    subscription_options,
)
from sincpro_framework.entrypoints.adapters.queue.inbox import Claimed, Inbox, inbox_key
from sincpro_framework.entrypoints.adapters.queue.verdicts import (
    SECURITY_KINDS,
    Settlement,
    Verdict,
    combined,
    failed,
)
from sincpro_framework.entrypoints.domain.bindings import QueueBinding
from sincpro_framework.entrypoints.domain.surface import Group, Operation, Resolved, Wire
from sincpro_framework.entrypoints.infrastructure import json_schema
from sincpro_framework.event_driven import ATTEMPTS_HEADER, Broker, Delivery, Envelope
from sincpro_framework.observability.metrics import Instrument, InstrumentKind, metrics
from sincpro_framework.observability.tracing.propagation import within_trace
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger

UNGUARDED = "unguarded"
QUEUE_DELIVERIES = Instrument(
    name="sincpro.queue.deliveries",
    kind=InstrumentKind.COUNTER,
    unit="{delivery}",
    description="Every delivery and how it was settled — ack, replay, skip, retry, dead letter",
    label_keys=(
        "messaging.system",
        "messaging.destination.name",
        "sincpro.settlement",
        "sincpro.failure_kind",
    ),
)
"""Dead letters rising is the alert; retries rising the warning before it."""
DEAD_LETTER_REASON = "sincpro-dead-letter-reason"
DEAD_LETTER_KIND = "sincpro-dead-letter-kind"
DEAD_LETTER_FROM = "sincpro-dead-letter-from"

type Identify = Callable[[Delivery, Envelope], str | None]
"""Who produced a message — the service's identity `producers=` is checked against."""
type Decode = Callable[[Delivery], Any]
"""A message as the DTO, or as the fields it validates from — full control's `decode=`."""


def by_envelope_source(message: Delivery, envelope: Envelope) -> str | None:
    """The CloudEvents `source` — trustworthy only inside one trust boundary: any client that
    can publish can write the header."""
    return envelope.source


def rabbit_user_id(message: Delivery, envelope: Envelope) -> str | None:
    """RabbitMQ's `user-id` property, which the broker validates against the connection's
    user — a producer cannot claim another's."""
    return getattr(message.raw_message, "user_id", None)


def _default_records() -> IdempotencyRecords:
    """The inbox a `QueueOptions` has by default — in this process's memory, a fresh one per
    options, enough for one replica."""
    return KeyValueRecords(InMemoryKeyValue())


def queue_context(envelope: Envelope, headers: Mapping[str, str]) -> dict[str, Any]:
    """What a message hands the execution's context — so every signal of the run says it
    (PRD_03 §4.10, PRD_21).

        queue_context(Envelope(correlationid="c-1", causationid="e-7"), {"x-tenant": "acme"})
        ->  {"correlation_id": "c-1", "causation_id": "e-7", "tenant_id": "acme"}

    1. The context the producer's execution carried (`baggage`, `sincpro-context`, the identity).
    2. The envelope's chain over it: `correlationid`, `causationid`.
    3. Final: the tenant a producer put in its own headers, when the context names none — as
       `tenant_id`, and as `tenant`, what this wire wrote before the key was standard.
    """
    carried: dict[str, Any] = context_propagation.extract(headers)
    if envelope.correlationid:
        carried[ContextKey.CORRELATION_ID] = envelope.correlationid
    if envelope.causationid:
        carried[ContextKey.CAUSATION_ID] = envelope.causationid
    tenant = headers.get("tenant") or headers.get("x-tenant")
    if tenant and ContextKey.TENANT_ID not in carried:
        carried[ContextKey.TENANT_ID] = tenant
    return carried


class QueueOptions(DataTransferObject):
    """How the queue wire settles — `None` is the broker's default (`flavours`)."""

    model_config = ConfigDict(frozen=True)

    inbox: IdempotencyRecords | None = Field(default_factory=_default_records)
    """Where deliveries are claimed — in memory by default (one replica); a shared store
    (`KeyValueRecords(RedisKeyValue(...))`) across replicas; `None` turns the inbox off."""
    max_attempts: int = 5
    """What a binding that declares none is tried at most before it is dead-lettered."""
    retry_by: RetryBy | None = None
    """How a retry is carried — `None`: the broker's default (`flavours`)."""
    dead_letter_by: DeadLetterBy | None = None
    """How a dead letter is carried — `None`: the broker's default (`flavours`)."""
    dead_letter_suffix: str = ".dlq"
    """What a dead-letter channel adds to the channel's name — `billing.issue.dlq`."""
    time_limit: timedelta | None = None
    """How long the bus may run one message — MUST stay below `in_progress_for` and the
    broker's visibility timeout. Past it the delivery is retried; the claim stays held, so a
    redelivery waits for it to expire rather than run beside the one still running."""
    in_progress_for: timedelta = timedelta(minutes=5)
    """How long the inbox's claim holds a delivery that is running — a replica that dies
    mid-run frees it once this passes."""
    keep_for: timedelta = timedelta(days=1)
    """How long a completed delivery is remembered — longer than a redelivery can take."""
    expires_after: timedelta | None = None
    """A message whose envelope `time` is older is dead-lettered — `EXPIRED`."""
    identify: Identify = by_envelope_source
    """Who produced a message — what `producers=` and the inbox key read; `rabbit_user_id`
    where the broker vouches for it."""
    subscription_of: SubscriptionOf | None = None
    """How a subscription is declared when the broker's defaults do not cover it — a RabbitMQ
    exchange, a Redis Stream (`flavours.SubscriptionOf`)."""


class Subscription(DataTransferObject):
    """One subscription the wire registered: a channel, a consumer group, and what it runs by
    message type — one Command, or the events heard in that group, each by every context of this
    process that hears it there."""

    model_config = ConfigDict(frozen=True)

    channel: str
    group: str
    kind: Literal["consumes", "hears"]
    by_type: dict[str, tuple[Resolved[QueueBinding], ...]]


def channel_of(resolved: Resolved[QueueBinding]) -> str:
    """The channel as subscribed: the group's prefix, then the binding's channel."""
    return f"{resolved.group.prefix or ''}{resolved.binding.channel or ''}"


def consumer_group_of(resolved: Resolved[QueueBinding]) -> str:
    """The consumer group a binding reads in — its own `group`, else the context's namespace,
    else the alias it was added under.

        QueueBinding(group="billing-workers")                  ->  "billing-workers"
        Group(alias="billing", namespace="acme.billing")       ->  "acme.billing"
        Group(alias="billing")                                 ->  "billing"
    """
    return resolved.binding.group or resolved.group.namespace or resolved.group.alias


def binding_name(resolved: Resolved[QueueBinding]) -> str:
    """The name the inbox keys a context's run under — `<alias>.<Command>`, so two contexts
    hearing one event each claim it on their own.

        binding_name(resolved)  ->  "billing.InvoiceIssued"
    """
    return f"{resolved.operation.alias}.{resolved.operation.command.__name__}"


def _decoded(command: type, body: bytes) -> Any:
    """The body as `command`: a DomainEvent from its JSON, a DTO validated once."""
    if isinstance(command, type) and issubclass(command, DomainEvent):
        return command.from_json(body.decode())  # type: ignore[attr-defined]
    if issubclass(command, BaseModel):
        return command.model_validate_json(body)
    return json_schema.adapter(command).validate_json(body)


def _validated(command: type, value: Any) -> Any:
    """What a project's `decode=` answered, made the Command — full control may hand back the
    DTO itself, raw text or bytes, or the fields.

        _validated(CommandIssue, CommandIssue(...))     ->  as it is
        _validated(CommandIssue, b'{"amount": 10}')     ->  decoded from JSON (`_decoded`)
        _validated(InvoiceIssued, {"id": "e-1", ...})   ->  the DomainEvent from its fields
        _validated(CommandIssue, {"amount": 10})        ->  CommandIssue(amount=10)
    """
    if isinstance(value, command):
        return value
    if isinstance(value, bytes | bytearray | str):
        return _decoded(command, value.encode() if isinstance(value, str) else bytes(value))
    if (
        isinstance(command, type)
        and issubclass(command, DomainEvent)
        and isinstance(value, dict)
    ):
        return command.from_json(json.dumps(value))  # type: ignore[attr-defined]
    if issubclass(command, BaseModel):
        return command.model_validate(value)
    return json_schema.adapter(command).validate_python(value)


def arrival_channel(message: Delivery) -> str | None:
    """The channel a message arrived on, where the broker's message says it.

        arrival_channel(<Kafka record, topic "billing.invoice.v1.issued">)
        ->  "billing.invoice.v1.issued"
        arrival_channel(<Redis, raw {"channel": b"billing.issue", ...}>)  ->  "billing.issue"
        arrival_channel(<SqsDelivery>)                                   ->  None

    1. The raw message's `topic` (Kafka), `routing_key` (RabbitMQ) or `subject` (NATS) —
       the first non-empty one.
    2. A raw message that is a dict (Redis): its `channel`, decoded when bytes.
    Final: `None` — the broker does not say.
    """
    raw = message.raw_message
    for attribute in ("topic", "routing_key", "subject"):
        value = getattr(raw, attribute, None)
        if isinstance(value, str) and value:
            return value
    if isinstance(raw, dict):
        value = raw.get("channel")
        if isinstance(value, bytes):
            return value.decode()
        if isinstance(value, str):
            return value
    return None


class QueueWire(Wire[QueueBinding]):
    """The queue wire on one broker — a FastStream one, or another `Broker` such as
    `SqsBroker`. Subclass it to derive otherwise and hand it to `QueueGateway(port=...)`.

        wire = QueueWire(KafkaBroker("kafka:9092"), QueueOptions(max_attempts=3))
        QueueGateway(wire.broker, [billing], port=wire).build()

    Context: the port derives, validates and builds like every wire's; past that it owns the
    one path a delivery takes (`process`) and how it is settled (`_settle`), from the broker's
    flavour and the options.
    """

    binding = QueueBinding

    def __init__(self, broker: Broker, options: QueueOptions | None = None) -> None:
        """Read the broker's flavour once and settle on it: the options' `retry_by` /
        `dead_letter_by` over the flavour's, and an `Inbox` unless the options turned it off.
        Nothing is subscribed until `build`.

            QueueWire(KafkaBroker("kafka:9092"))
            ->  retry_by REPUBLISH, dead_letter_by CHANNEL, an in-memory inbox
            QueueWire(RabbitBroker(url), QueueOptions(inbox=None))
            ->  retry_by NACK, dead_letter_by NATIVE, no inbox

        1. The broker stored, and the options given — else the defaults.
        2. The broker's flavour (`flavour_of`): its name and whether it acknowledges.
        3. `retry_by` and `dead_letter_by`: the options', else the flavour's.
        4. An `Inbox` over the options' records, claim and retention times — none when
           `inbox=None`.
        Final: not unguarded until the gateway says so; nothing subscribed yet.
        """
        self.broker = broker
        self.options = options or QueueOptions()
        flavour = flavour_of(broker)
        self.flavour = flavour
        self.broker_name: BrokerName = flavour.name
        self.acknowledges = flavour.acknowledges
        self.retry_by = self.options.retry_by or flavour.retry_by
        self.dead_letter_by = self.options.dead_letter_by or flavour.dead_letter_by
        self.inbox = (
            None
            if self.options.inbox is None
            else Inbox(
                self.options.inbox, self.options.in_progress_for, self.options.keep_for
            )
        )
        self.unguarded = False
        """Set by the gateway: its buses have no AccessControl on purpose."""
        self.subscriptions: list[Subscription] | None = None

    # derive / validate / build — the Wire port

    def derive(self, operation: Operation, group: Group) -> QueueBinding:
        """An event is heard on its wire name; a Command is consumed from the channel it
        declares — never derived, so it is never consumed by default."""
        if isinstance(operation.command, type) and issubclass(operation.command, DomainEvent):
            return QueueBinding(kind="hears", channel=registered_name(operation.command, ""))
        return QueueBinding(kind="consumes")

    def name_of(self, resolved: Resolved[QueueBinding]) -> str:
        """A queue operation is named by its channel — the manifest's `name`."""
        return channel_of(resolved)

    def _access_problem(self, resolved: Resolved[QueueBinding]) -> str | None:
        """The fallback rule on a queue: a Command says who may send it — `producers=`, or
        what the use case declares to its bus's AccessControl. An event is exempt: it reacts
        to what already happened, and the bus's own guard still runs on it."""
        if resolved.binding.kind == "hears":
            return None
        operation = resolved.operation
        where = f"{operation.command.__name__} (queue)"
        if operation.access is None:
            return (
                f"{where}: published on a bus guarded by AccessControl, but declares neither "
                "@auth.requires nor @auth.public — say who may send it"
            )
        if (
            operation.access == UNGUARDED
            and not self.unguarded
            and not resolved.binding.producers
        ):
            return (
                f"{where}: its bus '{operation.bus.name}' has no AccessControl — declare "
                "producers=, guard it, or tell the gateway unguarded=True"
            )
        return None

    def validate(self, surface: Sequence[Resolved[QueueBinding]]) -> list[str]:
        """Every reason the surface cannot be served on the broker — empty when it can.

            validate([<billing:CommandIssue on "billing.issue">,
                      <sales:CommandIssue on "billing.issue">])
            ->  ["queue: command channel billing.issue is consumed by billing:CommandIssue,
                 sales:CommandIssue — point to point runs exactly one handler"]
            <QueueOptions(time_limit=10 min, in_progress_for=5 min)> validate([...])
            ->  ["queue: time_limit 0:10:00 is not below the inbox's in_progress_for ..."]

        1. The time limit stays below the inbox's claim.
        2. Every binding says who may send it; attempts and concurrency are positive.
        3. Final: one command channel has exactly one handler, and carries no events. An
           event heard by several contexts in one group is one subscription running them all.
        """
        problems: list[str] = []
        limit = self.options.time_limit
        if limit is not None and limit >= self.options.in_progress_for:
            problems.append(
                f"queue: time_limit {limit} is not below the inbox's in_progress_for "
                f"{self.options.in_progress_for} — a redelivery would run beside the first"
            )
        consumed: dict[str, list[str]] = {}
        heard: set[str] = set()
        for one in surface:
            command = one.operation.command.__name__
            problem = self._access_problem(one)
            if problem:
                problems.append(problem)
            binding = one.binding
            for field in ("max_attempts", "concurrency"):
                value = getattr(binding, field)
                if value is not None and value < 1:
                    problems.append(f"{command} (queue): {field}={value} — at least 1")
            channel = channel_of(one)
            if binding.kind == "consumes":
                consumed.setdefault(channel, []).append(f"{one.operation.alias}:{command}")
            else:
                heard.add(channel)
        for channel, commands in consumed.items():
            if len(commands) > 1:
                problems.append(
                    f"queue: command channel {channel} is consumed by {', '.join(commands)} — "
                    "point to point runs exactly one handler"
                )
            if channel in heard:
                problems.append(
                    f"queue: {channel} is a command channel and also carries events — give "
                    "the Command a channel of its own"
                )
        return problems

    def subscriptions_of(
        self, surface: Sequence[Resolved[QueueBinding]]
    ) -> list[Subscription]:
        """The surface as subscriptions: one per channel, consumer group and kind, each with
        what it runs by message type.

            [IssueInvoice consumes "billing.issue", InvoiceIssued heard by billing and
             accounting on "billing.invoice.v1.issued" in group "billing"]
            ->  [Subscription("billing.issue", ..., by_type={"billing.IssueInvoice": (…,)}),
                 Subscription("billing.invoice.v1.issued", "billing", "hears",
                              by_type={"billing.invoice.v1.issued": (billing, accounting)})]

        Context: contexts that hear one event in one group share a subscription — the broker
        delivers once to the group, and `process` runs every context.
        """
        grouped: dict[tuple[str, str, str], dict[str, list[Resolved[QueueBinding]]]] = {}
        for one in surface:
            key = (channel_of(one), consumer_group_of(one), one.binding.kind)
            grouped.setdefault(key, {}).setdefault(
                registered_name(one.operation.command, one.operation.bus.name), []
            ).append(one)
        return [
            Subscription(
                channel=channel,
                group=group,
                kind=kind,  # type: ignore[arg-type]
                by_type={name: tuple(handlers) for name, handlers in by_type.items()},
            )
            for (channel, group, kind), by_type in grouped.items()
        ]

    def _pick(
        self, by_type: dict[str, tuple[Resolved[QueueBinding], ...]], envelope: Envelope
    ) -> tuple[Resolved[QueueBinding], ...]:
        """A command channel has one handler, whatever the type says; an event channel routes
        by type — a message without one goes to its only event, if it has only one."""
        handlers = list(by_type.values())
        if len(handlers) == 1 and (
            handlers[0][0].binding.kind == "consumes" or envelope.type is None
        ):
            return handlers[0]
        return by_type.get(envelope.type or "", ())

    def _attempts(self, message: Delivery, envelope: Envelope) -> int:
        """This delivery's attempt: the higher of what the attempts header says (a counted
        copy) and what the broker counted — at least 1."""
        counted = delivery_count(self.broker_name, message)
        return max(envelope.attempts, counted or 1)

    def _expired(self, envelope: Envelope) -> bool:
        """Whether the message is older than `expires_after` by its envelope's `time` — never
        when either is missing; a naive `time` is read as UTC."""
        limit = self.options.expires_after
        if limit is None or envelope.time is None:
            return False
        sent = envelope.time if envelope.time.tzinfo else envelope.time.replace(tzinfo=UTC)
        return datetime.now(UTC) - sent > limit

    # the one path every delivery takes

    async def _run(
        self,
        resolved: Resolved[QueueBinding],
        dto: Any,
        headers: dict[str, str],
        envelope: Envelope,
    ) -> None:
        """Run one context's bus on the DTO, off the event loop, as the producer's execution
        goes on.

        1. The headers become the queue's credentials, read as lower-case names.
        2. The context the message carried (`queue_context`).
        3. In a worker thread: authenticated and inside that context, the bus runs the DTO —
           identity is a `ContextVar`, so it is set in the thread that runs.
        4. Final: inside the producer's trace; past `time_limit` it raises `TimeoutError`, the
           thread left to finish on its own.
        """
        bus = resolved.operation.bus
        credentials = Credentials(
            transport="queue",
            headers={name.lower(): value for name, value in headers.items()},
        )

        carried = queue_context(envelope, credentials.headers)

        def call() -> Any:
            """The bus run in the worker thread, authenticated and in the carried context."""
            with (
                security.authenticated_as(bus, credentials),
                bus.context(carried, kind=EntrypointKind.QUEUE),
            ):
                return bus(dto)

        with within_trace(envelope.carrier()):
            running = asyncio.to_thread(call)
            limit = self.options.time_limit
            if limit is None:
                await running
            else:
                await asyncio.wait_for(running, limit.total_seconds())

    async def _handled(
        self,
        resolved: Resolved[QueueBinding],
        dto: Any,
        message: Delivery,
        message_id: str,
        envelope: Envelope,
        identity: str | None,
        attempts: int,
        refused: Callable[[exceptions.FailureKind, str], Verdict],
    ) -> Verdict:
        """One context's run of a delivery.

        1. The inbox claims `inbox:{binding}:{source}:{id}` — per context, so a retry re-runs
           only the context that failed: a completed redelivery is replayed without running;
           one running elsewhere is retried; another body under the same id is dead-lettered.
        2. The bus runs: success completes the claim; a failure releases it (past the time
           limit it stays held) and is classified.
        Final: this context's verdict.
        """
        name = registered_name(resolved.operation.command, resolved.operation.bus.name)
        key = inbox_key(binding_name(resolved), identity, message_id)
        owner: str | None = None
        if self.inbox is not None:
            claimed, owner = await self.inbox.claim(key, message.body)
            if claimed == Claimed.COMPLETED:
                return Verdict(settlement=Settlement.REPLAY, attempts=attempts, command=name)
            if claimed == Claimed.IN_PROGRESS:
                return refused(exceptions.FailureKind.IN_PROGRESS, "IN_PROGRESS")
            if claimed == Claimed.KEY_REUSED:
                return refused(exceptions.FailureKind.KEY_REUSED, "KEY_REUSED")
        try:
            await self._run(resolved, dto, Envelope.text_headers(message.headers), envelope)
        except TimeoutError:
            return refused(exceptions.FailureKind.UNAVAILABLE, "TIME_LIMIT")
        except Exception as error:
            if self.inbox is not None and owner is not None:
                await self.inbox.release(key, owner)
            kind = refined_failure_kind(error)
            return refused(kind, failure_reason(error, kind))
        if self.inbox is not None and owner is not None:
            await self.inbox.complete(key, message.body, owner)
        return Verdict(settlement=Settlement.ACK, attempts=attempts, command=name)

    def _copy_headers(
        self, message: Delivery, envelope: Envelope, attempts: int
    ) -> dict[str, str]:
        """The headers a republished copy carries: the original's as text, the envelope as
        CloudEvents headers, `sincpro-attempts` set to `attempts` — and RabbitMQ's
        `x-delivery-count` dropped, since it counted the original, not the copy."""
        headers = Envelope.text_headers(message.headers)
        headers.pop("x-delivery-count", None)
        headers.update(envelope.as_headers())
        headers[ATTEMPTS_HEADER] = str(attempts)
        return headers

    async def _publish_copy(
        self, message: Delivery, channel: str, headers: dict[str, str]
    ) -> bool:
        """Publish a copy of the message to `channel` — `False`, logged, when the broker
        refused it, so the caller nacks instead of acking a message that went nowhere."""
        try:
            await self.broker.publish(
                message.body,
                channel,
                headers=headers,
                **republish_options(self.broker_name, message),
            )
        except Exception:
            logger.exception(f"queue: could not publish to {channel}; the message is nacked")
            return False
        return True

    async def _settle(
        self,
        message: Delivery,
        verdict: Verdict,
        channel: str,
        envelope: Envelope,
    ) -> None:
        """1. Handled, replayed, skipped: ack.
        2. Retry: nack, or a copy with the attempt counted then ack — nack when the copy fails.
        3. Dead letter: reject (the broker's own), or a copy with the reason on
           `{channel}{suffix}` then ack — nack when the copy fails, so nothing is lost.
        """
        settlement = verdict.settlement
        metrics.emit(
            QUEUE_DELIVERIES,
            1,
            {
                "messaging.system": self.broker_name,
                "messaging.destination.name": channel,
                "sincpro.settlement": str(settlement),
                "sincpro.failure_kind": str(verdict.kind or ""),
            },
        )
        if settlement in (Settlement.ACK, Settlement.REPLAY, Settlement.SKIP):
            await message.ack()
            return
        if verdict.kind in SECURITY_KINDS:
            logger.warning(
                f"queue security: {verdict.command} on {channel} refused — {verdict.reason} "
                f"(source {envelope.source!r}, id {envelope.id!r})"
            )
        if settlement == Settlement.RETRY:
            logger.warning(
                f"queue: {verdict.command} on {channel} retried — {verdict.reason}, attempt "
                f"{verdict.attempts}"
            )
            if self.retry_by == RetryBy.NACK:
                await message.nack()
                return
            headers = self._copy_headers(message, envelope, verdict.attempts + 1)
            if await self._publish_copy(message, channel, headers):
                await message.ack()
            else:
                await message.nack()
            return
        logger.error(
            f"queue: {verdict.command} on {channel} dead-lettered — {verdict.kind}: "
            f"{verdict.reason} after {verdict.attempts} attempt(s)"
        )
        if self.dead_letter_by == DeadLetterBy.NATIVE:
            await message.reject()
            return
        headers = self._copy_headers(message, envelope, verdict.attempts)
        headers[DEAD_LETTER_REASON] = verdict.reason or ""
        headers[DEAD_LETTER_KIND] = str(verdict.kind or "")
        headers[DEAD_LETTER_FROM] = channel
        if await self._publish_copy(
            message, f"{channel}{self.options.dead_letter_suffix}", headers
        ):
            await message.ack()
        else:
            await message.nack()

    async def process(
        self,
        message: Delivery,
        by_type: dict[str, tuple[Resolved[QueueBinding], ...]],
        channel: str,
        decode: Decode | None = None,
        as_given: bool = False,
    ) -> Verdict:
        """One delivery, from its envelope to its settlement.

            await wire.process(delivery, subscription.by_type, "billing.invoice.v1.issued")
            ->  Verdict(settlement=Settlement.ACK, ...) — every context that hears it ran
            await wire.process(<sent by "shop", producers=("erp",)>, by_type, channel)
            ->  Verdict(settlement=DEAD_LETTER, reason="PRODUCER_NOT_ALLOWED"), never run
            await wire.process(<body "not json">, by_type, channel)
            ->  Verdict(settlement=DEAD_LETTER, reason="UNDECODABLE")

        1. What it is: an event nobody here handles on a shared channel is skipped (ack).
        2. Who sent it: a producer a binding does not allow is dead-lettered, before the bus.
        3. A message past `expires_after` is dead-lettered.
        4. Decoded as the Command, once: what does not decode is dead-lettered.
        5. Each context that handles it runs on its own (`_handled`), handed the message as the
           class *it* declared for that wire name — a context that may not import the publisher
           declares its own, and another context's instance would find no handler.
        Final: settled from the verdicts combined (`combined`), which is answered.
        """
        envelope = Envelope.of(message.headers)
        handlers = next(iter(by_type.values())) if as_given else self._pick(by_type, envelope)
        if not handlers:
            verdict = Verdict(settlement=Settlement.SKIP, command=envelope.type)
            await self._settle(message, verdict, channel, envelope)
            return verdict
        command = handlers[0].operation.command
        name = registered_name(command, handlers[0].operation.bus.name)
        attempts = self._attempts(message, envelope)
        max_attempts = min(
            one.binding.max_attempts or self.options.max_attempts for one in handlers
        )
        identity = self.options.identify(message, envelope)

        def refused(kind: exceptions.FailureKind, reason: str) -> Verdict:
            """A failure's verdict for this delivery — its attempts, the lowest `max_attempts`
            of its handlers, its type."""
            return failed(kind, reason, attempts, max_attempts, name)

        verdict: Verdict | None = None
        if any(
            one.binding.producers and identity not in one.binding.producers
            for one in handlers
        ):
            verdict = refused(
                exceptions.FailureKind.PERMISSION_DENIED, "PRODUCER_NOT_ALLOWED"
            )
        elif self._expired(envelope):
            verdict = refused(exceptions.FailureKind.INVALID, "EXPIRED")
        if verdict is not None:
            await self._settle(message, verdict, channel, envelope)
            return verdict

        try:
            dto = (
                _validated(command, decode(message))
                if decode
                else _decoded(command, message.body)
            )
        except Exception:
            verdict = refused(exceptions.FailureKind.INVALID, "UNDECODABLE")
            await self._settle(message, verdict, channel, envelope)
            return verdict

        message_id = (
            envelope.id
            or (dto.id if isinstance(dto, DomainEvent) else None)
            or message.message_id
        )
        envelope = envelope.model_copy(
            update={"id": message_id, "type": envelope.type or name}
        )
        verdicts: list[Verdict] = []
        for one in handlers:
            own = one.operation.command
            try:
                as_known = dto if isinstance(dto, own) else _decoded(own, message.body)
            except Exception:
                verdicts.append(refused(exceptions.FailureKind.INVALID, "UNDECODABLE"))
                continue
            verdicts.append(
                await self._handled(
                    one, as_known, message, message_id, envelope, identity, attempts, refused
                )
            )
        verdict = combined(verdicts)
        await self._settle(message, verdict, channel, envelope)
        return verdict

    # settling

    def _handler(self, subscription: Subscription) -> Callable[..., Any]:
        """What the broker calls on every delivery — FastStream hands the message by its
        `Context`, any other broker as the argument."""
        if self.flavour.faststream:
            from faststream import Context

            async def handle(message: Any = Context("message")) -> None:
                """FastStream's subscriber: the message from its `Context`, then `process`."""
                await self.process(message, subscription.by_type, subscription.channel)

        else:

            async def handle(message: Any = None) -> None:
                """Another broker's subscriber: the delivery as argument, then `process`."""
                await self.process(message, subscription.by_type, subscription.channel)

        handle.__name__ = handle.__qualname__ = "_".join(
            (
                "consume" if subscription.kind == "consumes" else "hear",
                *sorted(subscription.by_type),
            )
        )
        return handle

    def _concurrency(self, subscription: Subscription) -> int | None:
        """How many deliveries a subscription runs at once — the highest `concurrency` its
        bindings declare, `None` (the broker's default) when none does."""
        declared = [
            one.binding.concurrency
            for handlers in subscription.by_type.values()
            for one in handlers
        ]
        return max((one for one in declared if one is not None), default=None)

    def build(self, surface: Sequence[Resolved[QueueBinding]]) -> list[Subscription]:
        """Register one subscription per channel and group on the broker — once; building
        again answers what was registered.

            wire.build([<billing hears InvoiceIssued>, <accounting hears InvoiceIssued>])
            ->  [Subscription(channel="billing.invoice.v1.issued", group="billing",
                              kind="hears", ...), <the same channel, group "accounting">]
            wire.build(surface)          ->  the same list again, nothing re-registered

        1. Built before: what was registered.
        2. The subscriptions of the surface (`subscriptions_of`).
        3. For each, its concurrency and the handler the broker calls (`_handler`), then:
           3.1 the options' `subscription_of`, when the project declares its own;
           3.2 otherwise `broker.subscriber(channel, description=..., ...)` with the
               flavour's options (`subscription_options`).
        Final: the subscriptions stored, and answered.
        """
        if self.subscriptions is not None:
            return list(self.subscriptions)
        subscriptions = self.subscriptions_of(surface)
        for subscription in subscriptions:
            concurrency = self._concurrency(subscription)
            handler = self._handler(subscription)
            if self.options.subscription_of is not None:
                self.options.subscription_of(
                    self.broker, subscription.channel, subscription.group, concurrency
                )(handler)
                continue
            options = subscription_options(self.flavour, subscription.group, concurrency)
            described = ", ".join(sorted(subscription.by_type))
            self.broker.subscriber(
                subscription.channel,
                description=f"{subscription.kind} {described}",
                **options,
            )(handler)
        self.subscriptions = subscriptions
        return list(subscriptions)


class EventsWire(QueueWire):
    """Every event on the channel its name maps to, all in one consumer group."""

    def __init__(
        self,
        broker: Broker,
        options: QueueOptions | None,
        channel_of_name: Callable[[str], str],
        group: str,
    ) -> None:
        """The queue wire, plus how an event's name maps to its channel and the one consumer
        group every event is heard in."""
        super().__init__(broker, options)
        self._channel_of_name = channel_of_name
        self._group = group

    def derive(self, operation: Operation, group: Group) -> QueueBinding:
        """An event on `channel_of_name(its name)`, in the shared group; anything else as the
        queue wire derives it.

            derive(InvoiceIssued)  ->  QueueBinding(kind="hears",
                                                    channel="billing.invoice.v1.issued",
                                                    group="planning+assurance")
        """
        derived = super().derive(operation, group)
        if derived.kind != "hears":
            return derived
        channel = self._channel_of_name(registered_name(operation.command, ""))
        return derived.model_copy(update={"channel": channel, "group": self._group})
