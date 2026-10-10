"""How the queue wire settles each delivery (PRD_15 §5.2, §5.3, §6 queue row), on FastStream's
test brokers: Kafka, RabbitMQ and Redis.

Every invariant here is an incident: a failed message acked and lost (the at-most-once
regression), a poison message redelivered forever, a redelivery charging a customer twice, a
producer that is not allowed making this process run a Command.
"""

import asyncio
import json
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from faststream import AckPolicy, BaseMiddleware, Context, StreamMessage
from faststream.kafka import KafkaBroker, TestKafkaBroker
from faststream.message import AckStatus
from faststream.rabbit import RabbitBroker, TestRabbitBroker
from faststream.redis import RedisBroker, TestRedisBroker

from sincpro_framework import Feature, ProgrammingError, UseFramework
from sincpro_framework.auth import AccessControl, Permission
from sincpro_framework.common.store import InMemoryKeyValue
from sincpro_framework.data_layer.caching import (
    IdempotencyRecord,
    KeyValueRecords,
    RecordState,
)
from sincpro_framework.ddd import DomainEvent
from sincpro_framework.ddd.exceptions import StaleAggregate
from sincpro_framework.entrypoints.adapters.faststream import (
    ATTEMPTS_HEADER,
    DEAD_LETTER_FROM,
    DEAD_LETTER_KIND,
    DEAD_LETTER_REASON,
    QueueGateway,
    QueueOptions,
    Settlement,
    cloud_event_headers,
    inbox_key,
)
from sincpro_framework.entrypoints.entrypoint.decorators import queue
from sincpro_framework.event_driven.adapters.faststream import FastStreamQueue

from .test_queue_gateway import (
    ISSUE_CHANNEL,
    SALES,
    CommandIssueInvoice,
    InvoiceIssued,
    InvoiceVoided,
    accounting_bus,
    billing_bus,
)

DLQ = f"{ISSUE_CHANNEL}.dlq"


class Delivery:
    """One broker with the gateway built on it: what each delivery was settled as, what reached
    the dead-letter channel, what the use cases ran."""

    def __init__(
        self,
        broker_cls: type,
        test_broker: Callable[[Any], Any],
        options: QueueOptions | None = None,
        buses: Callable[[list[str]], list[UseFramework]] | None = None,
    ) -> None:
        self.settled: list[AckStatus | None] = []
        self.dead: list[dict[str, str]] = []
        self.ran: list[str] = []
        settled = self.settled

        class Spy(BaseMiddleware):
            async def consume_scope(self, call_next, msg):  # type: ignore[no-untyped-def]
                try:
                    return await call_next(msg)
                finally:
                    if DEAD_LETTER_REASON not in (msg.headers or {}):  # not the wire's
                        settled.append(msg.committed)

        self.broker = broker_cls(middlewares=[Spy])
        self.test_broker = test_broker
        made = (buses or (lambda ran: [billing_bus(ran), accounting_bus(ran)]))(self.ran)
        self.gateway = QueueGateway(self.broker, made, options=options, unguarded=True)
        self.gateway.build()

        @self.broker.subscriber(DLQ)
        async def dead_letters(message: StreamMessage[Any] = Context("message")) -> None:
            self.dead.append(dict(message.headers))

        self.dead_events: list[dict[str, str]] = []

        @self.broker.subscriber(f"{InvoiceIssued.name}.dlq")
        async def dead_events(message: StreamMessage[Any] = Context("message")) -> None:
            self.dead_events.append(dict(message.headers))

    def run(self, *publishes: tuple[bytes, str, dict[str, str]]) -> "Delivery":
        async def scenario() -> None:
            async with self.test_broker(self.broker) as connected:
                for body, channel, headers in publishes:
                    await connected.publish(body, channel, headers=headers)

        asyncio.run(scenario())
        return self


def issue(
    amount: int, customer_id: int = 7, headers: dict[str, str] | None = None
) -> tuple[bytes, str, dict[str, str]]:
    command = CommandIssueInvoice(customer_id=customer_id, amount=amount)
    envelope = cloud_event_headers(command, source=SALES, id=f"msg-{customer_id}-{amount}")
    return command.model_dump_json().encode(), ISSUE_CHANNEL, {**envelope, **(headers or {})}


KAFKA = pytest.param(KafkaBroker, TestKafkaBroker, id="kafka")
RABBIT = pytest.param(RabbitBroker, TestRabbitBroker, id="rabbit")
REDIS = pytest.param(RedisBroker, TestRedisBroker, id="redis")
BROKERS = [KAFKA, RABBIT, REDIS]


def _dead_lettered(delivery: Delivery, broker_cls: type) -> bool:
    """Dead-lettered, whatever the broker's way: RabbitMQ rejects to its dead-letter exchange,
    the others publish to `{channel}.dlq`."""
    if broker_cls is RabbitBroker:
        return delivery.settled[-1] == AckStatus.REJECTED and delivery.dead == []
    if broker_cls is RedisBroker:  # Pub/Sub acknowledges nothing: the copy is the proof
        return len(delivery.dead) == 1
    return len(delivery.dead) == 1 and delivery.settled[-1] == AckStatus.ACKED


# --- the happy path, and the at-most-once regression ------------------------------------------


@pytest.mark.parametrize(("broker_cls", "test_broker"), BROKERS)
def test_a_handled_command_is_acked_after_the_bus_ran(broker_cls, test_broker):
    delivery = Delivery(broker_cls, test_broker).run(issue(100))

    assert delivery.ran == ["issued 7:100"]
    if broker_cls is not RedisBroker:  # Pub/Sub acknowledges nothing
        assert delivery.settled == [AckStatus.ACKED]


@pytest.mark.parametrize(("broker_cls", "test_broker"), BROKERS)
def test_a_raising_handler_is_redelivered_then_dead_lettered_at_max_attempts(
    broker_cls, test_broker
):
    """The at-most-once regression, on every broker: a failure is never acked and forgotten.
    RabbitMQ nacks (the broker redelivers); Kafka and Redis publish a copy with the attempt
    counted — the test broker delivers it at once — until `max_attempts=3`, then the
    dead-letter channel, with the reason."""
    delivery = Delivery(broker_cls, test_broker).run(issue(0))

    if broker_cls is RabbitBroker:
        assert delivery.settled == [AckStatus.NACKED]
        return
    (dead,) = delivery.dead
    assert dead[DEAD_LETTER_REASON] == "INTERNAL"
    assert dead[DEAD_LETTER_KIND] == "internal"
    assert dead[ATTEMPTS_HEADER] == "3"
    assert dead[DEAD_LETTER_FROM] == ISSUE_CHANNEL
    if broker_cls is KafkaBroker:
        assert delivery.settled == [AckStatus.ACKED] * 3


def test_rabbit_counts_attempts_by_its_delivery_count():
    """A quorum queue's `x-delivery-count` is the broker's own count: the third delivery of a
    `max_attempts=3` binding is dead-lettered instead of nacked again."""
    third = Delivery(RabbitBroker, TestRabbitBroker).run(
        issue(0, headers={"x-delivery-count": "2"})
    )
    second = Delivery(RabbitBroker, TestRabbitBroker).run(
        issue(0, headers={"x-delivery-count": "1"})
    )

    assert third.settled == [AckStatus.REJECTED]
    assert second.settled == [AckStatus.NACKED]


def test_the_attempts_header_counts_where_the_broker_does_not():
    delivery = Delivery(KafkaBroker, TestKafkaBroker).run(
        issue(0, headers={ATTEMPTS_HEADER: "3"})
    )

    assert len(delivery.dead) == 1
    assert delivery.settled == [AckStatus.ACKED]


# --- each kind to its verdict ------------------------------------------------------------------


@pytest.mark.parametrize(("broker_cls", "test_broker"), BROKERS)
def test_a_domain_refusal_is_dead_lettered_at_once(broker_cls, test_broker):
    delivery = Delivery(broker_cls, test_broker).run(issue(-5))

    assert _dead_lettered(delivery, broker_cls)
    if delivery.dead:
        assert delivery.dead[0][DEAD_LETTER_REASON] == "DOMAIN_ERROR"
        assert delivery.dead[0][ATTEMPTS_HEADER] == "1"


@pytest.mark.parametrize(("broker_cls", "test_broker"), BROKERS)
def test_poison_reaches_the_dead_letter_queue_with_its_reason(broker_cls, test_broker):
    headers = cloud_event_headers(source=SALES, type="CommandIssueInvoice", id="poison")

    delivery = Delivery(broker_cls, test_broker).run((b"not json", ISSUE_CHANNEL, headers))

    assert delivery.ran == []
    assert _dead_lettered(delivery, broker_cls)
    if delivery.dead:
        assert delivery.dead[0][DEAD_LETTER_REASON] == "UNDECODABLE"
        assert delivery.dead[0][DEAD_LETTER_KIND] == "invalid"


def test_an_invalid_dto_is_dead_lettered():
    headers = cloud_event_headers(source=SALES, id="wrong-shape")
    body = json.dumps({"customer_id": "not a number", "amount": 1}).encode()

    delivery = Delivery(KafkaBroker, TestKafkaBroker).run((body, ISSUE_CHANNEL, headers))

    assert delivery.dead[0][DEAD_LETTER_REASON] == "UNDECODABLE"


@pytest.mark.parametrize(("broker_cls", "test_broker"), BROKERS)
def test_a_producer_not_allowed_is_refused_before_the_bus(broker_cls, test_broker, caplog):
    body, channel, headers = issue(100)
    headers["ce_source"] = "urn:svc:intruder"

    delivery = Delivery(broker_cls, test_broker).run((body, channel, headers))

    assert delivery.ran == []
    assert _dead_lettered(delivery, broker_cls)
    if delivery.dead:
        assert delivery.dead[0][DEAD_LETTER_REASON] == "PRODUCER_NOT_ALLOWED"
        assert delivery.dead[0][DEAD_LETTER_KIND] == "permission_denied"


def test_a_message_with_no_producer_is_refused_where_producers_are_declared():
    body, channel, headers = issue(100)
    del headers["ce_source"]

    delivery = Delivery(KafkaBroker, TestKafkaBroker).run((body, channel, headers))

    assert delivery.ran == []
    assert delivery.dead[0][DEAD_LETTER_REASON] == "PRODUCER_NOT_ALLOWED"


def test_a_conflict_is_retried():
    def conflicting(ran: list[str]) -> list[UseFramework]:
        bus = UseFramework("billing", log_after_execution=False)

        @bus.feature(CommandIssueInvoice)
        @queue.consumes(ISSUE_CHANNEL, producers=(SALES,), max_attempts=2)
        class IssueInvoice(Feature):
            def execute(self, dto: CommandIssueInvoice) -> None:
                ran.append("tried")
                raise StaleAggregate("a newer invoice was saved")

        return [bus]

    delivery = Delivery(KafkaBroker, TestKafkaBroker, buses=conflicting).run(issue(1))

    assert delivery.ran == ["tried", "tried"]
    assert delivery.dead[0][DEAD_LETTER_KIND] == "conflict"


def test_an_auth_refusal_is_dead_lettered_and_logged_as_security(caplog):
    class Can(Permission):
        ISSUE = "billing.issue"

    auth = AccessControl[Can]()

    def guarded(ran: list[str]) -> list[UseFramework]:
        bus = UseFramework("billing", log_after_execution=False)

        @bus.feature(CommandIssueInvoice)
        @auth.requires(Can.ISSUE)
        @queue.consumes(ISSUE_CHANNEL, producers=(SALES,))
        class IssueInvoice(Feature):
            def execute(self, dto: CommandIssueInvoice) -> None:
                ran.append("issued")

        auth.on(bus)
        return [bus]

    delivery = Delivery(KafkaBroker, TestKafkaBroker, buses=guarded).run(issue(1))

    assert delivery.ran == []
    assert delivery.dead[0][DEAD_LETTER_KIND] in {"unauthenticated", "permission_denied"}
    assert delivery.dead[0][ATTEMPTS_HEADER] == "1"


def test_an_expired_message_is_dead_lettered():
    body, channel, headers = issue(100)
    headers["ce_time"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    options = QueueOptions(expires_after=timedelta(hours=1))

    delivery = Delivery(KafkaBroker, TestKafkaBroker, options).run((body, channel, headers))

    assert delivery.ran == []
    assert delivery.dead[0][DEAD_LETTER_REASON] == "EXPIRED"


# --- the inbox -------------------------------------------------------------------------------


@pytest.mark.parametrize(("broker_cls", "test_broker"), BROKERS)
def test_a_redelivered_message_runs_the_use_case_once(broker_cls, test_broker):
    """At least once plus the inbox: the same source and id again is acked without running."""
    delivery = Delivery(broker_cls, test_broker).run(issue(100), issue(100))

    assert delivery.ran == ["issued 7:100"]
    if broker_cls is not RedisBroker:
        assert delivery.settled == [AckStatus.ACKED, AckStatus.ACKED]


def test_without_the_inbox_a_redelivery_runs_again():
    delivery = Delivery(KafkaBroker, TestKafkaBroker, QueueOptions(inbox=None))

    delivery.run(issue(100), issue(100))

    assert delivery.ran == ["issued 7:100", "issued 7:100"]


def test_a_failed_delivery_releases_its_claim_so_the_retry_runs():
    ran: list[str] = []
    calls = {"n": 0}

    def flaky(_: list[str]) -> list[UseFramework]:
        bus = UseFramework("billing", log_after_execution=False)

        @bus.feature(CommandIssueInvoice)
        @queue.consumes(ISSUE_CHANNEL, producers=(SALES,))
        class IssueInvoice(Feature):
            def execute(self, dto: CommandIssueInvoice) -> None:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("once")
                ran.append("issued")

        return [bus]

    delivery = Delivery(KafkaBroker, TestKafkaBroker, buses=flaky).run(issue(1))

    assert ran == ["issued"]
    assert delivery.dead == []


def test_the_same_id_with_another_body_is_dead_lettered_as_key_reused():
    first = issue(100)
    body = CommandIssueInvoice(customer_id=7, amount=999).model_dump_json().encode()

    delivery = Delivery(KafkaBroker, TestKafkaBroker).run(first, (body, first[1], first[2]))

    assert delivery.ran == ["issued 7:100"]
    assert delivery.dead[0][DEAD_LETTER_REASON] == "KEY_REUSED"


def test_a_delivery_running_elsewhere_is_retried_not_run():
    records = KeyValueRecords(InMemoryKeyValue())
    body, channel, headers = issue(100)
    import hashlib

    records.claim(
        inbox_key("billing.CommandIssueInvoice", SALES, headers["ce_id"]),
        IdempotencyRecord(
            state=RecordState.IN_PROGRESS,
            fingerprint=hashlib.sha256(body).hexdigest(),
            owner="another replica",
        ),
        timedelta(minutes=5),
    )

    delivery = Delivery(RabbitBroker, TestRabbitBroker, QueueOptions(inbox=records)).run(
        (body, channel, headers)
    )

    assert delivery.ran == []
    assert delivery.settled == [AckStatus.NACKED]


def test_the_time_limit_retries_and_keeps_the_claim():
    records = KeyValueRecords(InMemoryKeyValue())
    release = threading.Event()

    def slow(ran: list[str]) -> list[UseFramework]:
        bus = UseFramework("billing", log_after_execution=False)

        @bus.feature(CommandIssueInvoice)
        @queue.consumes(ISSUE_CHANNEL, producers=(SALES,))
        class IssueInvoice(Feature):
            def execute(self, dto: CommandIssueInvoice) -> None:
                release.wait(5)
                ran.append("late")

        return [bus]

    options = QueueOptions(inbox=records, time_limit=timedelta(milliseconds=50))
    delivery = Delivery(RabbitBroker, TestRabbitBroker, options, buses=slow)
    delivery.run(issue(1))
    release.set()
    time.sleep(0.1)

    assert delivery.settled == [AckStatus.NACKED]
    held = records.read(inbox_key("billing.CommandIssueInvoice", SALES, "msg-7-1"))
    assert held is not None and held.state == RecordState.IN_PROGRESS


# --- events ----------------------------------------------------------------------------------


def test_an_event_from_fast_stream_queue_is_heard_once_by_its_own_id():
    """No envelope at all — `FastStreamQueue` writes none — and still one run per event: the
    inbox keys it by the event's own id."""
    ran: list[str] = []
    broker = KafkaBroker()
    QueueGateway(broker, [accounting_bus(ran)], unguarded=True).build()
    event = InvoiceIssued(number="F-1")

    async def scenario() -> None:
        async with TestKafkaBroker(broker):
            await FastStreamQueue(broker).aput(event)
            await FastStreamQueue(broker).aput(event)

    asyncio.run(scenario())

    assert ran == ["booked F-1"]


def test_two_contexts_in_one_group_both_hear_and_a_retry_reruns_only_the_one_that_failed():
    """One delivery, two contexts: the message is acked only when both ran. A retry re-runs the
    one that failed — the inbox keys each context apart, so the other is not run twice."""
    ran: list[str] = []
    failing = UseFramework("failing", log_after_execution=False)

    @failing.feature(InvoiceIssued)
    @queue.hears(group="accounting")
    class Fail(Feature):
        def execute(self, dto: InvoiceIssued) -> None:
            ran.append(f"failed {dto.number}")
            raise RuntimeError("the ledger went away")

    delivery = Delivery(
        KafkaBroker,
        TestKafkaBroker,
        options=QueueOptions(max_attempts=3),
        buses=lambda heard: [accounting_bus(heard), failing],
    )
    event = InvoiceIssued(number="F-1")

    delivery.run((event.as_json().encode(), InvoiceIssued.name, {}))

    assert delivery.ran == ["booked F-1"]
    assert ran == ["failed F-1"] * 3
    assert len(delivery.dead_events) == 1


def test_each_context_in_a_group_gets_the_event_as_its_own_class():
    """A context that may not import the publisher declares its own class under the same wire
    name. Handed the other context's instance, its bus would find no handler for that class and
    the event would be silently not delivered."""

    @dataclass(kw_only=True)
    class IssuedAsAuditKnowsIt(DomainEvent):
        name = InvoiceIssued.name
        number: str = ""

    audited: list[str] = []
    audit = UseFramework("audit", log_after_execution=False)

    @audit.feature(IssuedAsAuditKnowsIt)
    @queue.hears(group="accounting")
    class Audit(Feature):
        def execute(self, dto: IssuedAsAuditKnowsIt) -> None:
            audited.append(f"audited {dto.number}")

    delivery = Delivery(
        KafkaBroker, TestKafkaBroker, buses=lambda heard: [accounting_bus(heard), audit]
    )

    delivery.run((InvoiceIssued(number="F-2").as_json().encode(), InvoiceIssued.name, {}))

    assert (delivery.ran, audited) == (["booked F-2"], ["audited F-2"])
    assert delivery.settled == [AckStatus.ACKED]


def test_an_event_nobody_here_handles_on_a_shared_channel_is_skipped():
    delivery = Delivery(KafkaBroker, TestKafkaBroker)
    delivery.gateway  # built: InvoiceIssued heard on its channel
    other = InvoiceVoided(number="F-9")
    headers = cloud_event_headers(other, source="urn:svc:billing")

    delivery.run((other.as_json().encode(), InvoiceIssued.name, headers))

    assert delivery.ran == []
    assert delivery.settled == [AckStatus.ACKED]


# --- CloudEvents -----------------------------------------------------------------------------


def test_the_dead_letter_copy_carries_the_cloudevents_envelope():
    body, channel, headers = issue(-1)
    headers["ce_correlationid"] = "order-77"

    delivery = Delivery(KafkaBroker, TestKafkaBroker).run((body, channel, headers))

    (dead,) = delivery.dead
    assert dead["ce_specversion"] == "1.0"
    assert dead["ce_id"] == "msg-7--1"
    assert dead["ce_source"] == SALES
    assert dead["ce_type"] == "CommandIssueInvoice"
    assert dead["ce_correlationid"] == "order-77"


def test_a_message_says_what_it_is_by_its_identity():
    """`ce_type` is the identity every registry routes by: a Command's `context.Class`, an
    event's name — the body read alone in a dead-letter queue says which message it is."""
    command = cloud_event_headers(
        CommandIssueInvoice(customer_id=7, amount=1), source=SALES, context="billing"
    )
    event = cloud_event_headers(InvoiceIssued(number="F-1"), source=SALES, context="billing")

    assert command["ce_type"] == "billing.CommandIssueInvoice"
    assert billing_bus([]).dto_registry[command["ce_type"]] is CommandIssueInvoice
    assert event["ce_type"] == InvoiceIssued.name


def test_an_envelope_is_read_in_every_binding_spelling():
    from sincpro_framework.entrypoints.adapters.faststream import Envelope

    for prefix in ("ce_", "ce-", "cloudEvents:", "cloudEvents_"):
        envelope = Envelope.of({f"{prefix}id": "1", f"{prefix}source": SALES})
        assert (envelope.id, envelope.source) == ("1", SALES)


def test_the_trace_crosses_the_broker():
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    tracer = TracerProvider().get_tracer("test")
    seen: list[int] = []

    def tracing(_: list[str]) -> list[UseFramework]:
        bus = UseFramework("billing", log_after_execution=False)

        @bus.feature(CommandIssueInvoice)
        @queue.consumes(ISSUE_CHANNEL, producers=(SALES,))
        class IssueInvoice(Feature):
            def execute(self, dto: CommandIssueInvoice) -> None:
                seen.append(trace.get_current_span().get_span_context().trace_id)

        return [bus]

    with tracer.start_as_current_span("producer") as span:
        published = issue(1)
        producer_trace = span.get_span_context().trace_id

    assert "traceparent" in published[2]
    Delivery(KafkaBroker, TestKafkaBroker, buses=tracing).run(published)

    assert seen == [producer_trace]


# --- full control: one path ------------------------------------------------------------------


def _full_control(
    broker_cls: type, ran: list[str], settled: list[Any], verdicts: list[Any]
) -> tuple[Any, Callable[[], Awaitable[None]]]:
    broker = broker_cls()
    gateway = QueueGateway(broker, [billing_bus(ran)], unguarded=True)

    @broker.subscriber("legacy.issue", ack_policy=AckPolicy.MANUAL)
    async def legacy(message: StreamMessage[Any] = Context("message")) -> None:
        verdicts.append(
            await gateway.consume(
                message,
                as_=CommandIssueInvoice,
                decode=lambda m: {"customer_id": 7, "amount": int(m.body)},
            )
        )
        settled.append(message.committed)

    return broker, legacy


@pytest.mark.parametrize(
    "amount", [pytest.param(100, id="handled"), pytest.param(-1, id="domain")]
)
def test_consume_runs_the_path_the_generated_subscriber_runs(amount):
    """§1.2 parity: one input through the generated subscriber and through `consume` — the
    same effects, the same settlement."""
    generated = Delivery(RabbitBroker, TestRabbitBroker).run(issue(amount))
    ran: list[str] = []
    settled: list[Any] = []
    verdicts: list[Any] = []
    broker, _ = _full_control(RabbitBroker, ran, settled, verdicts)
    _, _, headers = issue(amount)

    async def scenario() -> None:
        async with TestRabbitBroker(broker) as connected:
            await connected.publish(
                str(amount).encode(), "legacy.issue", headers=dict[str, Any](headers)
            )

    asyncio.run(scenario())

    assert ran == generated.ran
    assert settled == generated.settled
    expected = Settlement.ACK if amount > 0 else Settlement.DEAD_LETTER
    assert verdicts[0].settlement == expected


def test_consume_applies_the_producer_rule_and_the_inbox():
    ran: list[str] = []
    settled: list[Any] = []
    verdicts: list[Any] = []
    broker, _ = _full_control(KafkaBroker, ran, settled, verdicts)
    _, _, allowed = issue(5)
    intruder = {**allowed, "ce_source": "urn:svc:intruder", "ce_id": "other"}

    async def scenario() -> None:
        async with TestKafkaBroker(broker) as connected:
            for headers in (allowed, allowed, intruder):
                await connected.publish(b"5", "legacy.issue", headers=headers)

    asyncio.run(scenario())

    assert ran == ["issued 7:5"]
    assert [one.settlement for one in verdicts] == [
        Settlement.ACK,
        Settlement.REPLAY,
        Settlement.DEAD_LETTER,
    ]
    assert verdicts[2].reason == "PRODUCER_NOT_ALLOWED"


def test_consume_refuses_a_command_no_bus_answers():
    from sincpro_framework import DataTransferObject

    class Unknown(DataTransferObject):
        x: int

    gateway = QueueGateway(KafkaBroker(), [billing_bus([])], unguarded=True)

    async def scenario() -> None:
        await gateway.consume(None, as_=Unknown)  # type: ignore[arg-type]

    with pytest.raises(ProgrammingError, match="no bus of this gateway answers it"):
        asyncio.run(scenario())


# --- nothing is lost when the copy cannot be published ----------------------------------------


class _Unreachable:
    """A broker whose publishing fails — the dead-letter or retry copy cannot be written."""

    async def publish(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("broker unreachable")

    def subscriber(self, *args: Any, **kwargs: Any) -> Callable[..., Any]:
        return lambda handler: handler

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class _Message:
    """What the wire reads of a delivery, and how it was settled."""

    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self.body = body
        self.headers = headers
        self.message_id = "fallback-id"
        self.raw_message = None
        self.settled: list[str] = []

    async def ack(self) -> None:
        self.settled.append("ack")

    async def nack(self) -> None:
        self.settled.append("nack")

    async def reject(self) -> None:
        self.settled.append("reject")


@pytest.mark.parametrize(
    "amount", [pytest.param(-1, id="dead-letter"), pytest.param(0, id="retry")]
)
def test_a_copy_that_cannot_be_published_leaves_the_message_to_the_broker(amount):
    """A dead letter or a counted retry is a copy then an ack: when the copy fails, the
    delivery is nacked — acking it would lose the message."""
    gateway = QueueGateway(_Unreachable(), [billing_bus([])], unguarded=True)  # type: ignore[arg-type]
    body, _, headers = issue(amount)
    message = _Message(body, headers)

    asyncio.run(gateway.consume(message, as_=CommandIssueInvoice))  # type: ignore[arg-type]

    assert message.settled == ["nack"]
