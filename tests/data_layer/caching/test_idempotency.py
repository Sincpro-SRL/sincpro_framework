"""`Idempotency`: a write runs once per key across replicas; a retry gets the first answer.

The write stands in for what a real one does that must never happen twice: issue a document on
another system. Two `Idempotency` objects on one store are two replicas behind a balancer.
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from structlog.testing import capture_logs

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.common.store import InMemoryKeyValue, KeyValueStore
from sincpro_framework.data_layer.caching import (
    IDEMPOTENCY_KEY,
    AlreadyInProgress,
    Idempotency,
    IdempotencyPolicy,
    IdempotencyRecords,
    JsonCodec,
    KeyReused,
    KeyValueRecords,
)
from sincpro_framework.data_layer.caching.adapters.redis import RedisKeyValue
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.runtime.testing import IdempotencyRecordsContract, ManualClock


class Receipt(DataTransferObject):
    number: int


@dataclass
class Issuer:
    issued: list[str] = field(default_factory=list)
    delay: float = 0.0

    def issue(self, what: str) -> Receipt:
        time.sleep(self.delay)
        self.issued.append(what)
        return Receipt(number=len(self.issued))


POLICY = IdempotencyPolicy(expires_after=timedelta(minutes=2))
STORES = ["memory", "redis"]


def _store(name: str, clock: ManualClock | None = None) -> KeyValueStore:
    if name == "memory":
        return InMemoryKeyValue(now=clock.now) if clock else InMemoryKeyValue()
    return RedisKeyValue(fakeredis.FakeRedis(), prefix="test:")


def _issue(
    idempotency: Idempotency,
    issuer: Issuer,
    key: str,
    what: str = "invoice",
    policy: IdempotencyPolicy = POLICY,
) -> Receipt:
    return idempotency.run(
        key, lambda: issuer.issue(what), policy, JsonCodec(Receipt), payload=what
    )


@pytest.mark.parametrize("store_name", STORES)
def test_a_retry_racing_the_first_call_on_another_replica_does_not_write_twice(store_name):
    """The incident: the first call is slow, the client times out and retries, the retry lands on
    the other replica while the first is still writing. Check-then-write lets both through."""
    store = _store(store_name)
    replicas = [Idempotency(store), Idempotency(store)]
    issuer = Issuer(delay=0.05)
    policy = IdempotencyPolicy(
        expires_after=timedelta(minutes=2), wait_for_completion=timedelta(seconds=2)
    )
    answers: list[Receipt] = []
    lock = threading.Lock()

    def call(replica: Idempotency) -> None:
        answer = _issue(replica, issuer, "request-1", policy=policy)
        with lock:
            answers.append(answer)

    threads = [threading.Thread(target=call, args=(replicas[n % 2],)) for n in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert issuer.issued == ["invoice"]
    assert answers == [Receipt(number=1)] * 10


@pytest.mark.parametrize("store_name", STORES)
def test_a_completed_write_is_replayed_without_running(store_name):
    idempotency = Idempotency(_store(store_name))
    issuer = Issuer()

    first = _issue(idempotency, issuer, "request-1")
    replay = _issue(idempotency, issuer, "request-1")

    assert first == replay == Receipt(number=1)
    assert issuer.issued == ["invoice"]


@pytest.mark.parametrize("store_name", STORES)
def test_a_failed_write_releases_its_key_and_the_retry_runs(store_name):
    """Only a completed answer is replayed: a failure replayed forever is a write nobody can
    ever retry."""
    idempotency = Idempotency(_store(store_name))
    issuer = Issuer()

    def failing() -> Receipt:
        raise ConnectionError("the other system did not answer")

    with pytest.raises(ConnectionError):
        idempotency.run("request-1", failing, POLICY, JsonCodec(Receipt), payload="invoice")

    assert _issue(idempotency, issuer, "request-1") == Receipt(number=1)


@pytest.mark.parametrize("store_name", STORES)
def test_a_key_reused_for_another_payload_is_refused_and_runs_nothing(store_name):
    idempotency = Idempotency(_store(store_name))
    issuer = Issuer()
    _issue(idempotency, issuer, "request-1", what="invoice")

    with pytest.raises(KeyReused):
        _issue(idempotency, issuer, "request-1", what="credit-note")

    assert issuer.issued == ["invoice"]


def test_a_duplicate_mid_run_is_told_it_is_in_progress_when_it_does_not_wait():
    store = InMemoryKeyValue()
    first, duplicate = Idempotency(store), Idempotency(store)
    started, release = threading.Event(), threading.Event()

    def slow() -> Receipt:
        started.set()
        release.wait(2)
        return Receipt(number=1)

    runner = threading.Thread(
        target=lambda: first.run("request-1", slow, POLICY, JsonCodec(Receipt))
    )
    runner.start()
    started.wait(2)
    try:
        with pytest.raises(AlreadyInProgress):
            duplicate.run("request-1", lambda: Receipt(number=99), POLICY, JsonCodec(Receipt))
    finally:
        release.set()
        runner.join()


def test_a_claim_left_by_a_replica_that_died_expires():
    """A claim that never completes cannot refuse its key forever."""
    clock = ManualClock(datetime(2026, 9, 29, 12, 0, tzinfo=UTC))
    store = InMemoryKeyValue(now=clock.now)
    idempotency = Idempotency(store)
    policy = IdempotencyPolicy(
        expires_after=timedelta(minutes=2), in_progress_for=timedelta(seconds=30)
    )
    store.add(
        idempotency._key("request-1"),
        b'{"state": "in_progress", "fingerprint": "", "owner": "dead"}\n',
        policy.in_progress_for,
    )
    with pytest.raises(AlreadyInProgress):
        idempotency.run("request-1", lambda: Receipt(number=1), policy, JsonCodec(Receipt))

    clock.advance(seconds=31)

    assert idempotency.run(
        "request-1", lambda: Receipt(number=1), policy, JsonCodec(Receipt)
    ) == Receipt(number=1)


def test_a_completed_answer_is_replayed_only_for_expires_after():
    clock = ManualClock(datetime(2026, 9, 29, 12, 0, tzinfo=UTC))
    idempotency = Idempotency(InMemoryKeyValue(now=clock.now))
    issuer = Issuer()

    _issue(idempotency, issuer, "request-1")
    clock.advance(minutes=3)
    _issue(idempotency, issuer, "request-1")

    assert issuer.issued == ["invoice", "invoice"]


class CommandIssueInvoice(DataTransferObject):
    customer: str
    total: int


class ResponseIssueInvoice(DataTransferObject):
    number: int


class CommandPing(DataTransferObject):
    pass


class ResponsePing(DataTransferObject):
    pong: int


def _billing(
    issued: list[str],
    idempotency: Idempotency,
    vary_by: tuple[str, ...] = (),
) -> UseFramework:
    billing = UseFramework("billing-idempotency", log_after_execution=False)
    billing.add_dependency("issued", issued)

    @billing.feature(CommandIssueInvoice)
    @idempotency.once(expires_after=timedelta(minutes=2), vary_by=vary_by)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            self.issued.append(dto.customer)
            return ResponseIssueInvoice(number=len(self.issued))

    @billing.feature(CommandPing)
    class Ping(Feature):
        def execute(self, dto: CommandPing) -> ResponsePing:
            self.issued.append("ping")
            return ResponsePing(pong=len(self.issued))

    return billing


def test_a_use_case_declared_once_runs_once_however_it_is_repeated():
    issued: list[str] = []
    billing = _billing(issued, Idempotency(InMemoryKeyValue()))

    first = billing(CommandIssueInvoice(customer="ana", total=10), ResponseIssueInvoice)
    retry = billing(CommandIssueInvoice(customer="ana", total=10), ResponseIssueInvoice)
    other = billing(CommandIssueInvoice(customer="luis", total=10), ResponseIssueInvoice)

    assert first == retry == ResponseIssueInvoice(number=1)
    assert other == ResponseIssueInvoice(number=2)
    assert issued == ["ana", "luis"]


def test_a_use_case_that_declares_nothing_runs_every_time():
    issued: list[str] = []
    billing = _billing(issued, Idempotency(InMemoryKeyValue()))

    billing(CommandPing(), ResponsePing)
    billing(CommandPing(), ResponsePing)

    assert issued == ["ping", "ping"]


def test_the_same_command_for_two_tenants_is_two_writes():
    """Two tenants sending identical arguments must never share an answer."""
    issued: list[str] = []
    billing = _billing(issued, Idempotency(InMemoryKeyValue()), vary_by=("tenant_id",))

    for tenant in ("acme", "globex"):
        with billing.context({"tenant_id": tenant}):
            billing(CommandIssueInvoice(customer="ana", total=10), ResponseIssueInvoice)

    assert issued == ["ana", "ana"]


class CommandIssueByRequest(DataTransferObject):
    request_id: str
    total: int

    def idempotency_key(self) -> str:
        return self.request_id


def test_the_command_says_its_key_and_the_rest_of_it_is_still_checked():
    """A client that reuses a request id for a different invoice is told so, not replayed the
    first invoice's answer as if it were the second's."""
    issued: list[int] = []
    idempotency = Idempotency(InMemoryKeyValue())
    billing = UseFramework("billing-by-request", log_after_execution=False)

    @billing.feature(CommandIssueByRequest)
    @idempotency.once(expires_after=timedelta(minutes=2))
    class IssueByRequest(Feature):
        def execute(self, dto: CommandIssueByRequest) -> ResponseIssueInvoice:
            issued.append(dto.total)
            return ResponseIssueInvoice(number=len(issued))

    first = billing(CommandIssueByRequest(request_id="r-1", total=10), ResponseIssueInvoice)
    retry = billing(CommandIssueByRequest(request_id="r-1", total=10), ResponseIssueInvoice)
    with pytest.raises(KeyReused):
        billing(CommandIssueByRequest(request_id="r-1", total=99), ResponseIssueInvoice)
    other = billing(CommandIssueByRequest(request_id="r-2", total=99), ResponseIssueInvoice)

    assert first == retry == ResponseIssueInvoice(number=1)
    assert other == ResponseIssueInvoice(number=2)
    assert issued == [10, 99]


def test_a_request_key_from_the_transport_keys_a_command_that_names_none():
    """An `Idempotency-Key` header reaches the bus context as `idempotency_key`: for a Command
    without its own key it is the key — two keys are two writes even with the same arguments, one
    key reused with other arguments is refused."""
    issued: list[str] = []
    billing = _billing(issued, Idempotency(InMemoryKeyValue()))
    invoice = CommandIssueInvoice(customer="ana", total=10)

    for key in ("k-1", "k-1", "k-2"):
        with billing.context({IDEMPOTENCY_KEY: key}):
            billing(invoice, ResponseIssueInvoice)
    with billing.context({IDEMPOTENCY_KEY: "k-1"}), pytest.raises(KeyReused):
        billing(CommandIssueInvoice(customer="luis", total=10), ResponseIssueInvoice)

    assert issued == ["ana", "ana"]


def test_the_commands_own_key_outranks_the_transports():
    issued: list[int] = []
    idempotency = Idempotency(InMemoryKeyValue())
    billing = UseFramework("billing-own-key", log_after_execution=False)

    @billing.feature(CommandIssueByRequest)
    @idempotency.once(expires_after=timedelta(minutes=2))
    class IssueByRequest(Feature):
        def execute(self, dto: CommandIssueByRequest) -> ResponseIssueInvoice:
            issued.append(dto.total)
            return ResponseIssueInvoice(number=len(issued))

    for key in ("k-1", "k-2"):
        with billing.context({IDEMPOTENCY_KEY: key}):
            billing(CommandIssueByRequest(request_id="r-1", total=10), ResponseIssueInvoice)

    assert issued == [10]


def test_a_subclass_that_keeps_execute_keeps_once():
    issued: list[str] = []
    idempotency = Idempotency(InMemoryKeyValue())
    billing = UseFramework("billing-subclass", log_after_execution=False)

    @idempotency.once(expires_after=timedelta(minutes=2))
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            issued.append(dto.customer)
            return ResponseIssueInvoice(number=len(issued))

    @billing.feature(CommandIssueInvoice)
    class IssueInvoiceHere(IssueInvoice):
        pass

    billing(CommandIssueInvoice(customer="ana", total=10), ResponseIssueInvoice)
    billing(CommandIssueInvoice(customer="ana", total=10), ResponseIssueInvoice)

    assert issued == ["ana"]


def test_whether_a_class_runs_once_is_asked_by_its_resolved_execute():
    """An entrypoint marks a use case retry-safe by asking, not by reading a method's name: a
    subclass that keeps `execute` is still once, a replacement with its own is not."""
    from sincpro_framework.data_layer.caching import declares_once

    idempotency = Idempotency(InMemoryKeyValue())

    @idempotency.once(expires_after=timedelta(minutes=2))
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number=1)

    class KeepsExecute(IssueInvoice):
        pass

    class OwnExecute(IssueInvoice):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number=2)

    class Plain(Feature):
        def execute(self, dto: CommandPing) -> ResponsePing:
            return ResponsePing(pong=1)

    assert declares_once(IssueInvoice) and declares_once(KeepsExecute)
    assert not declares_once(OwnExecute)
    assert not declares_once(Plain)
    assert not declares_once(int)


def test_once_twice_on_one_class_and_on_an_async_use_case_are_refused():
    idempotency = Idempotency(InMemoryKeyValue())

    with pytest.raises(ContractViolation, match="twice"):

        @idempotency.once(expires_after=timedelta(minutes=2))
        @idempotency.once(expires_after=timedelta(minutes=2))
        class Twice(Feature):
            def execute(self, dto: CommandPing) -> ResponsePing:
                return ResponsePing(pong=1)

    with pytest.raises(ContractViolation, match="async"):

        @idempotency.once(expires_after=timedelta(minutes=2))
        class Asynchronous(Feature):
            async def execute(self, dto: CommandPing) -> ResponsePing:  # type: ignore[override]
                return ResponsePing(pong=1)


def test_an_async_caller_racing_duplicates_through_the_async_bus_writes_once():
    """What async callers have: the bus is synchronous and refuses an `async def execute`, so an
    async entrypoint reaches a use case through `get_async_bus()`, on a worker thread — and a
    burst of retries fanned out with `asyncio.gather` must still write once."""
    import asyncio

    issued: list[str] = []
    idempotency = Idempotency(InMemoryKeyValue())
    billing = UseFramework("billing-async-callers", log_after_execution=False)

    @billing.feature(CommandIssueInvoice)
    @idempotency.once(
        expires_after=timedelta(minutes=2), wait_for_completion=timedelta(seconds=2)
    )
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            time.sleep(0.05)
            issued.append(dto.customer)
            return ResponseIssueInvoice(number=len(issued))

    async def burst() -> list[ResponseIssueInvoice | None]:
        bus = billing.get_async_bus()
        command = CommandIssueInvoice(customer="ana", total=10)
        return list(
            await asyncio.gather(*(bus(command, ResponseIssueInvoice) for _ in range(6)))
        )

    answers = asyncio.run(burst())

    assert issued == ["ana"]
    assert answers == [ResponseIssueInvoice(number=1)] * 6


def test_a_replayed_answer_is_still_authorized():
    """The guard runs inside the access guard: an answer kept for one caller is not handed to
    someone who may not run the use case."""
    from sincpro_framework.auth import AccessControl, Permission, PermissionDenied
    from sincpro_framework.runtime.testing import granting

    class BillingPermission(Permission):
        ISSUE = "billing.invoice.issue"

    issued: list[str] = []
    idempotency = Idempotency(InMemoryKeyValue())
    billing = UseFramework("billing-authorized", log_after_execution=False)
    auth = AccessControl[BillingPermission]()
    auth.on(billing)

    @billing.feature(CommandIssueInvoice)
    @auth.requires(BillingPermission.ISSUE)
    @idempotency.once(expires_after=timedelta(minutes=2))
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            issued.append(dto.customer)
            return ResponseIssueInvoice(number=len(issued))

    with granting(BillingPermission.ISSUE):
        billing(CommandIssueInvoice(customer="ana", total=10), ResponseIssueInvoice)
    with granting(subject="user:other"), pytest.raises(PermissionDenied):
        billing(CommandIssueInvoice(customer="ana", total=10), ResponseIssueInvoice)

    assert issued == ["ana"]


def test_a_use_case_whose_answer_has_no_type_cannot_be_declared_once():
    idempotency = Idempotency(InMemoryKeyValue())
    billing = UseFramework("billing-untyped", log_after_execution=False)

    @billing.feature(CommandPing)
    @idempotency.once(expires_after=timedelta(minutes=2))
    class Ping(Feature):
        def execute(self, dto):  # type: ignore[no-untyped-def]
            return ResponsePing(pong=1)

    with pytest.raises(ContractViolation, match="declares no response type"):
        billing(CommandPing(), ResponsePing)


def test_the_run_knows_its_key_to_hand_on_downstream():
    """A system called inside the write deduplicates by the same key: a write that ran twice
    after a crash is still one write there."""
    from sincpro_framework.data_layer.caching import current_idempotency_key

    seen: list[str | None] = []
    idempotency = Idempotency(InMemoryKeyValue())

    idempotency.run(
        "request-1",
        lambda: seen.append(current_idempotency_key()) or Receipt(number=1),
        POLICY,
        JsonCodec(Receipt),
    )

    assert seen[0] is not None and seen[0].startswith("idempotency:")
    assert current_idempotency_key() is None


def _deployed(
    issued: list[str],
    idempotency: Idempotency,
    added_to_command: bool,
    added_to_response: bool,
) -> tuple[UseFramework, type, type]:
    """`billing-deploys` as one deployment builds it: a newer one may have added a field with a
    default to its Command, or a required one to its response."""

    class CommandIssueOnce(DataTransferObject):
        customer: str
        total: int
        if added_to_command:
            channel: str = "web"

    class ResponseIssueOnce(DataTransferObject):
        number: int
        if added_to_response:
            series: str

    billing = UseFramework("billing-deploys", log_after_execution=False)
    billing.add_dependency("issued", issued)

    @billing.feature(CommandIssueOnce)
    @idempotency.once(expires_after=timedelta(minutes=2))
    class IssueOnce(Feature):
        def execute(self, dto: CommandIssueOnce) -> ResponseIssueOnce:
            self.issued.append(dto.customer)
            return ResponseIssueOnce(number=len(self.issued), series="B")

    return billing, CommandIssueOnce, ResponseIssueOnce


def test_a_retry_across_a_deploy_that_added_a_defaulted_field_keeps_its_key():
    """The key is the Command's identity and the fields it sets apart from their defaults: the
    newer deployment's retry of the same request is the same request."""
    issued: list[str] = []
    store = InMemoryKeyValue()
    older, OlderCommand, _ = _deployed(issued, Idempotency(store), False, False)
    newer, NewerCommand, NewerResponse = _deployed(issued, Idempotency(store), True, False)

    older(OlderCommand(customer="ana", total=10))
    retry = newer(NewerCommand(customer="ana", total=10), NewerResponse)

    assert retry == NewerResponse(number=1) and issued == ["ana"]


def test_a_kept_answer_the_newer_response_cannot_read_runs_again_and_says_so():
    issued: list[str] = []
    store = InMemoryKeyValue()
    older, OlderCommand, _ = _deployed(issued, Idempotency(store), False, False)
    newer, NewerCommand, NewerResponse = _deployed(issued, Idempotency(store), False, True)
    older(OlderCommand(customer="ana", total=10))

    with capture_logs() as logs:
        again = newer(NewerCommand(customer="ana", total=10), NewerResponse)
    replayed = newer(NewerCommand(customer="ana", total=10), NewerResponse)

    assert again == replayed == NewerResponse(number=2, series="B")
    assert issued == ["ana", "ana"]
    assert any("runs again" in line["event"] for line in logs)


class TestKeyValueRecordsOnMemory(IdempotencyRecordsContract):
    def make_records(self) -> IdempotencyRecords:
        return KeyValueRecords(InMemoryKeyValue())


class TestKeyValueRecordsOnRedis(IdempotencyRecordsContract):
    def make_records(self) -> IdempotencyRecords:
        return KeyValueRecords(RedisKeyValue(fakeredis.FakeRedis(), prefix="test:"))


def test_every_outcome_is_reported_to_the_observer():
    """Without these counted, a duplicate refused or replayed is invisible in production."""
    from sincpro_framework.data_layer.caching import CountingObserver, IdempotencyOutcome

    observer = CountingObserver()
    store = InMemoryKeyValue()
    idempotency = Idempotency(store, namespace="billing", observer=observer)
    issuer = Issuer()

    _issue(idempotency, issuer, "r-1")
    _issue(idempotency, issuer, "r-1")
    with pytest.raises(KeyReused):
        _issue(idempotency, issuer, "r-1", what="other")
    store.add(
        idempotency._key("r-2"),
        b'{"state": "in_progress", "fingerprint": "", "owner": "x"}\n',
        timedelta(minutes=1),
    )
    with pytest.raises(AlreadyInProgress):
        idempotency.run("r-2", lambda: Receipt(number=0), POLICY, JsonCodec(Receipt))

    assert observer.of(IdempotencyOutcome.CLAIMED, "billing") == 1
    assert observer.of(IdempotencyOutcome.REPLAYED, "billing") == 1
    assert observer.of(IdempotencyOutcome.KEY_REUSED, "billing") == 1
    assert observer.of(IdempotencyOutcome.IN_PROGRESS, "billing") == 1


def test_an_observer_that_raises_never_breaks_the_write():
    class Broken:
        def observed(self, outcome: str, namespace: str) -> None:
            raise RuntimeError("metrics backend down")

    idempotency = Idempotency(InMemoryKeyValue(), observer=Broken())

    assert _issue(idempotency, Issuer(), "r-1") == Receipt(number=1)


def test_a_record_that_vanishes_between_the_claim_and_the_read_is_claimed_again():
    """The claim lost to a record that then expired: the caller must claim again, not fail."""

    class Racing(KeyValueRecords):
        def __init__(self) -> None:
            super().__init__(InMemoryKeyValue())
            self.lost_once = False

        def claim(self, key, record, hold) -> bool:  # type: ignore[no-untyped-def]
            if not self.lost_once:
                self.lost_once = True
                return False
            return super().claim(key, record, hold)

    idempotency = Idempotency(Racing())

    assert _issue(idempotency, Issuer(), "r-1") == Receipt(number=1)


def test_the_declared_policies_are_described():
    idempotency = Idempotency(InMemoryKeyValue())
    billing = _billing([], idempotency)

    assert list(idempotency.policies().values()) == [POLICY]
    del billing


def test_the_default_observer_puts_each_outcome_on_the_active_span():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    idempotency = Idempotency(InMemoryKeyValue(), namespace="billing")

    with provider.get_tracer(__name__).start_as_current_span("IssueInvoice"):
        _issue(idempotency, Issuer(), "r-1")
        _issue(idempotency, Issuer(), "r-1")

    (span,) = exporter.get_finished_spans()
    attributes = [dict(event.attributes or {}) for event in span.events]
    assert [one["outcome"] for one in attributes] == ["claimed", "replayed"]
    assert {one["namespace"] for one in attributes} == {"billing"}
