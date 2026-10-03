"""`QueueWire`: the queue wire on a FastStream broker — what it derives, what it refuses, the
subscriptions it registers, and the one path every delivery takes (PRD_15 §1.2, §5).

    envelope → producer allowed? → expired? → decode → inbox claim → bus(dto) → verdict → settle
                                                         └ identity, access guard, idempotency,
                                                           caching, interceptors — the bus's own

Context: the generated subscriber and `QueueGateway.consume` (full control) both call `process`,
so a hand-written subscriber can neither skip nor re-implement a stage. Every subscription
settles manually from the verdict; the bus runs in a worker thread, inside the producer's trace,
authenticated in that same thread (identity is a `ContextVar`).
"""

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from faststream import Context, StreamMessage
from pydantic import BaseModel, ConfigDict, Field

from sincpro_framework.auth.domain import Credentials
from sincpro_framework.auth.transports import authenticated_as
from sincpro_framework.caching import IdempotencyRecords, InMemoryKeyValue, KeyValueRecords
from sincpro_framework.context.adapters.propagation import extract
from sincpro_framework.context.domain.execution import CAUSATION_ID, CORRELATION_ID
from sincpro_framework.context.domain.keys import TENANT_ID
from sincpro_framework.context.domain.level import EntrypointKind
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.entrypoints import json_utils
from sincpro_framework.entrypoints.exposure import (
    Group,
    Operation,
    QueueBinding,
    Resolved,
    Wire,
)
from sincpro_framework.entrypoints.faststream.envelope import (
    ATTEMPTS_HEADER,
    Envelope,
    text_headers,
    type_name,
)
from sincpro_framework.entrypoints.faststream.flavours import (
    BrokerName,
    DeadLetterBy,
    RetryBy,
    SubscriptionOf,
    broker_name,
    delivery_count,
    flavour_of,
    republish_options,
    subscription_options,
)
from sincpro_framework.entrypoints.faststream.inbox import Claimed, Inbox, inbox_key
from sincpro_framework.entrypoints.faststream.verdicts import (
    SECURITY_KINDS,
    Settlement,
    Verdict,
    combined,
    failed,
)
from sincpro_framework.event_driven.adapters.faststream.queue import Broker
from sincpro_framework.event_driven.infrastructure.trace import within_trace
from sincpro_framework.observability.metrics.domain.instruments import (
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.infrastructure.active import active
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.transport.failures import (
    FailureKind,
    failure_reason,
    refined_failure_kind,
)

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

type Identify = Callable[[StreamMessage[Any], Envelope], str | None]
"""Who produced a message — the service's identity `producers=` is checked against."""
type Decode = Callable[[StreamMessage[Any]], Any]
"""A message as the DTO, or as the fields it validates from — full control's `decode=`."""


def by_envelope_source(message: StreamMessage[Any], envelope: Envelope) -> str | None:
    """The CloudEvents `source` — trustworthy only inside one trust boundary: any client that
    can publish can write the header."""
    return envelope.source


def rabbit_user_id(message: StreamMessage[Any], envelope: Envelope) -> str | None:
    """RabbitMQ's `user-id` property, which the broker validates against the connection's
    user — a producer cannot claim another's."""
    return getattr(message.raw_message, "user_id", None)


def _default_records() -> IdempotencyRecords:
    return KeyValueRecords(InMemoryKeyValue())


def queue_context(envelope: Envelope, headers: Mapping[str, str]) -> dict[str, Any]:
    """What a message hands the execution's context — so every signal of the run says it
    (PRD_03 §4.10, PRD_21).

    1. The context the producer's execution carried (`baggage`, `sincpro-context`, the identity).
    2. The envelope's chain over it: `correlationid`, `causationid`.
    3. Final: the tenant a producer put in its own headers, when the context names none — as
       `tenant_id`, and as `tenant`, what this wire wrote before the key was standard.
    """
    carried: dict[str, Any] = extract(headers)
    if envelope.correlationid:
        carried[CORRELATION_ID] = envelope.correlationid
    if envelope.causationid:
        carried[CAUSATION_ID] = envelope.causationid
    tenant = headers.get("tenant") or headers.get("x-tenant")
    if tenant and TENANT_ID not in carried:
        carried[TENANT_ID] = carried["tenant"] = tenant
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
    dead_letter_by: DeadLetterBy | None = None
    dead_letter_suffix: str = ".dlq"
    time_limit: timedelta | None = None
    """How long the bus may run one message — MUST stay below `in_progress_for` and the
    broker's visibility timeout. Past it the delivery is retried; the claim is kept, so a
    redelivery waits for it to expire rather than run beside the one still running."""
    in_progress_for: timedelta = timedelta(minutes=5)
    keep_for: timedelta = timedelta(days=1)
    """How long a completed delivery is remembered — longer than a redelivery can take."""
    expires_after: timedelta | None = None
    """A message whose envelope `time` is older is dead-lettered — `EXPIRED`."""
    identify: Identify = by_envelope_source
    subscription_of: SubscriptionOf | None = None


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
    return resolved.binding.group or resolved.group.namespace or resolved.group.alias


def binding_name(resolved: Resolved[QueueBinding]) -> str:
    return f"{resolved.operation.alias}.{resolved.operation.command.__name__}"


def _is_event(command: type) -> bool:
    return isinstance(command, type) and issubclass(command, DomainEvent)


def _decoded(command: type, body: bytes) -> Any:
    """The body as `command`: a DomainEvent from its JSON, a DTO validated once."""
    if _is_event(command):
        return command.from_json(body.decode())  # type: ignore[attr-defined]
    if issubclass(command, BaseModel):
        return command.model_validate_json(body)
    return json_utils.adapter(command).validate_json(body)


def _validated(command: type, value: Any) -> Any:
    if isinstance(value, command):
        return value
    if isinstance(value, bytes | bytearray | str):
        return _decoded(command, value.encode() if isinstance(value, str) else bytes(value))
    if _is_event(command) and isinstance(value, dict):
        return command.from_json(json.dumps(value))  # type: ignore[attr-defined]
    if issubclass(command, BaseModel):
        return command.model_validate(value)
    return json_utils.adapter(command).validate_python(value)


def arrival_channel(message: StreamMessage[Any]) -> str | None:
    """The channel a message arrived on, where the broker's message says it."""
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
    """The queue wire on one FastStream broker. Subclass it to derive otherwise and hand it to
    `QueueGateway(port=...)`."""

    binding = QueueBinding

    def __init__(self, broker: Broker, options: QueueOptions | None = None) -> None:
        self.broker = broker
        self.options = options or QueueOptions()
        flavour = flavour_of(broker)
        self.broker_name: BrokerName = broker_name(broker)
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
        if _is_event(operation.command):
            return QueueBinding(kind="hears", channel=type_name(operation.command))
        return QueueBinding(kind="consumes")

    def name_of(self, resolved: Resolved[QueueBinding]) -> str:
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
        """1. The time limit stays below the inbox's claim.
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
        grouped: dict[tuple[str, str, str], dict[str, list[Resolved[QueueBinding]]]] = {}
        for one in surface:
            key = (channel_of(one), consumer_group_of(one), one.binding.kind)
            grouped.setdefault(key, {}).setdefault(
                type_name(one.operation.command), []
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

    def _handler(self, subscription: Subscription) -> Callable[..., Any]:
        async def handle(message: StreamMessage[Any] = Context("message")) -> None:
            await self.process(message, subscription.by_type, subscription.channel)

        handle.__name__ = handle.__qualname__ = "_".join(
            (
                "consume" if subscription.kind == "consumes" else "hear",
                *sorted(subscription.by_type),
            )
        )
        return handle

    def _concurrency(self, subscription: Subscription) -> int | None:
        declared = [
            one.binding.concurrency
            for handlers in subscription.by_type.values()
            for one in handlers
        ]
        return max((one for one in declared if one is not None), default=None)

    def build(self, surface: Sequence[Resolved[QueueBinding]]) -> list[Subscription]:
        """Register one subscription per channel and group on the broker — once; building
        again answers what was registered."""
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
            options = subscription_options(self.broker_name, subscription.group, concurrency)
            described = ", ".join(sorted(subscription.by_type))
            self.broker.subscriber(
                subscription.channel,
                description=f"{subscription.kind} {described}",
                **options,
            )(handler)
        self.subscriptions = subscriptions
        return list(subscriptions)

    # the one path every delivery takes

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

    def _attempts(self, message: StreamMessage[Any], envelope: Envelope) -> int:
        counted = delivery_count(self.broker_name, message)
        return max(envelope.attempts, counted or 1)

    def _expired(self, envelope: Envelope) -> bool:
        limit = self.options.expires_after
        if limit is None or envelope.time is None:
            return False
        sent = envelope.time if envelope.time.tzinfo else envelope.time.replace(tzinfo=UTC)
        return datetime.now(UTC) - sent > limit

    async def _run(
        self,
        resolved: Resolved[QueueBinding],
        dto: Any,
        headers: dict[str, str],
        envelope: Envelope,
    ) -> None:
        bus = resolved.operation.bus
        credentials = Credentials(
            transport="queue",
            headers={name.lower(): value for name, value in headers.items()},
        )

        carried = queue_context(envelope, credentials.headers)

        def call() -> Any:
            with (
                authenticated_as(bus, credentials),
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

    async def process(
        self,
        message: StreamMessage[Any],
        by_type: dict[str, tuple[Resolved[QueueBinding], ...]],
        channel: str,
        decode: Decode | None = None,
        as_given: bool = False,
    ) -> Verdict:
        """One delivery, from its envelope to its settlement.

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
        name = type_name(command)
        attempts = self._attempts(message, envelope)
        max_attempts = min(
            one.binding.max_attempts or self.options.max_attempts for one in handlers
        )
        identity = self.options.identify(message, envelope)

        def refused(kind: FailureKind, reason: str) -> Verdict:
            return failed(kind, reason, attempts, max_attempts, name)

        verdict: Verdict | None = None
        if any(
            one.binding.producers and identity not in one.binding.producers
            for one in handlers
        ):
            verdict = refused(FailureKind.PERMISSION_DENIED, "PRODUCER_NOT_ALLOWED")
        elif self._expired(envelope):
            verdict = refused(FailureKind.INVALID, "EXPIRED")
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
            verdict = refused(FailureKind.INVALID, "UNDECODABLE")
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
                verdicts.append(refused(FailureKind.INVALID, "UNDECODABLE"))
                continue
            verdicts.append(
                await self._handled(
                    one, as_known, message, message_id, envelope, identity, attempts, refused
                )
            )
        verdict = combined(verdicts)
        await self._settle(message, verdict, channel, envelope)
        return verdict

    async def _handled(
        self,
        resolved: Resolved[QueueBinding],
        dto: Any,
        message: StreamMessage[Any],
        message_id: str,
        envelope: Envelope,
        identity: str | None,
        attempts: int,
        refused: Callable[[FailureKind, str], Verdict],
    ) -> Verdict:
        """One context's run of a delivery.

        1. The inbox claims `inbox:{binding}:{source}:{id}` — per context, so a retry re-runs
           only the context that failed: a completed redelivery is replayed without running;
           one running elsewhere is retried; another body under the same id is dead-lettered.
        2. The bus runs: success completes the claim; a failure releases it (the time limit
           keeps it) and is classified.
        Final: this context's verdict.
        """
        name = type_name(resolved.operation.command)
        key = inbox_key(binding_name(resolved), identity, message_id)
        owner: str | None = None
        if self.inbox is not None:
            claimed, owner = await self.inbox.claim(key, message.body)
            if claimed == Claimed.COMPLETED:
                return Verdict(settlement=Settlement.REPLAY, attempts=attempts, command=name)
            if claimed == Claimed.IN_PROGRESS:
                return refused(FailureKind.IN_PROGRESS, "IN_PROGRESS")
            if claimed == Claimed.KEY_REUSED:
                return refused(FailureKind.KEY_REUSED, "KEY_REUSED")
        try:
            await self._run(resolved, dto, text_headers(message.headers), envelope)
        except TimeoutError:
            return refused(FailureKind.UNAVAILABLE, "TIME_LIMIT")
        except Exception as error:
            if self.inbox is not None and owner is not None:
                await self.inbox.release(key, owner)
            kind = refined_failure_kind(error)
            return refused(kind, failure_reason(error, kind))
        if self.inbox is not None and owner is not None:
            await self.inbox.complete(key, message.body, owner)
        return Verdict(settlement=Settlement.ACK, attempts=attempts, command=name)

    # settling

    def _copy_headers(
        self, message: StreamMessage[Any], envelope: Envelope, attempts: int
    ) -> dict[str, str]:
        headers = text_headers(message.headers)
        headers.pop("x-delivery-count", None)
        headers.update(envelope.as_headers())
        headers[ATTEMPTS_HEADER] = str(attempts)
        return headers

    async def _publish_copy(
        self, message: StreamMessage[Any], channel: str, headers: dict[str, str]
    ) -> bool:
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
        message: StreamMessage[Any],
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
        active.emit(
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
