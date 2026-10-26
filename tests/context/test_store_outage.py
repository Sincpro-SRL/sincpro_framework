"""The context store goes down — every call goes on in this process's memory, and the replicas
catch up when it answers again. Only what promises to persist fails."""

import threading
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from structlog.testing import capture_logs

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.context import (
    KeyValueContexts,
    context_propagation,
    store_context_in,
    use_context,
)
from sincpro_framework.context.domain.level import EntrypointKind
from sincpro_framework.context.infrastructure import outage
from sincpro_framework.context.infrastructure.shared import SharedValues
from sincpro_framework.data_layer.store import (
    InMemoryKeyValue,
    KeyValueStore,
    StoreUnavailable,
)


class FlakyKeyValue(KeyValueStore):
    """A store in memory that stops answering while `down` — every operation counted."""

    def __init__(self) -> None:
        self.inner = InMemoryKeyValue()
        self.down = False
        self.asked = 0

    def _reached(self) -> InMemoryKeyValue:
        self.asked += 1
        if self.down:
            raise StoreUnavailable("FlakyKeyValue did not answer: ConnectionError")
        return self.inner

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        return self._reached().get_many(keys)

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        self._reached().set(key, value, ttl)

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        return self._reached().add(key, value, ttl)

    def increment(self, key: str, ttl: timedelta | None = None) -> int:
        return self._reached().increment(key, ttl)

    def delete(self, key: str) -> None:
        self._reached().delete(key)

    def take(self, key: str) -> bytes | None:
        return self._reached().take(key)

    def replace(
        self, key: str, expected: bytes, value: bytes | None, ttl: timedelta | None = None
    ) -> bool:
        return self._reached().replace(key, expected, value, ttl)

    def shared(self) -> bool:
        return True


class CommandRead(DataTransferObject):
    pass


class CommandWrite(DataTransferObject):
    pass


@pytest.fixture
def asked_at_once(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No pause after a failure: every attempt asks the store, so a test sees it come back."""
    monkeypatch.setattr(outage, "OUTAGE_BYPASS", timedelta(0))
    yield


def _bus(name: str, read: list[Any]) -> UseFramework:
    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(CommandRead)
    class Read(Feature):
        def execute(self, dto: CommandRead) -> None:
            read.append(use_context().get("tenant_id"))

    @bus.feature(CommandWrite)
    class Write(Feature):
        def execute(self, dto: CommandWrite) -> None:
            use_context().set("step", 1)
            read.append(use_context().get("step"))

    return bus


# --- every call goes on -------------------------------------------------------------------------


def test_joining_a_store_that_does_not_answer_never_fails():
    flaky = FlakyKeyValue()
    flaky.down = True
    read: list[Any] = []
    bus = _bus("outage-joining", read)

    store_context_in(KeyValueContexts(flaky))
    bus.store_context_in(KeyValueContexts(flaky))
    with bus.context({"tenant_id": "acme"}):
        bus(CommandRead())

    assert read == ["acme"]


def test_a_call_reads_and_writes_its_context_while_the_store_is_down():
    flaky = FlakyKeyValue()
    read: list[Any] = []
    bus = _bus("outage-calls", read)
    bus.store_context_in(KeyValueContexts(flaky))
    flaky.down = True

    with bus.context({"tenant_id": "acme"}):
        bus(CommandRead())
        bus(CommandWrite())
    with bus.context({"plan": "pro"}, global_scope=True):
        bus(CommandRead())

    assert read == ["acme", 1, None]


def test_while_down_the_store_is_not_asked_again_on_every_call():
    flaky = FlakyKeyValue()
    read: list[Any] = []
    bus = _bus("outage-not-asked", read)
    bus.store_context_in(KeyValueContexts(flaky), every=timedelta(0))
    flaky.down = True

    with bus.context({"tenant_id": "acme"}):
        bus(CommandRead())
    asked = flaky.asked
    with bus.context({"tenant_id": "acme"}):
        for _ in range(20):
            bus(CommandWrite())

    assert flaky.asked == asked and len(read) == 21


# --- the replicas catch up ----------------------------------------------------------------------


def test_what_each_replica_wrote_while_down_reaches_the_others_when_it_answers(asked_at_once):
    flaky = FlakyKeyValue()
    store = KeyValueContexts(flaky)
    pod_a = SharedValues(store, "process:billing", timedelta(0), seed={"lang": "es"})
    pod_b = SharedValues(store, "process:billing", timedelta(0))
    flaky.down = True

    pod_a.set("plan", "pro")
    pod_b.set("maintenance", True)
    pod_b.set("plan", "enterprise")  # the same key, written last
    assert pod_a.current["plan"] == "pro"  # each reads its own meanwhile
    flaky.down = False

    pod_a.current  # each sends what waited, in order
    pod_b.current
    expected = {"lang": "es", "maintenance": True, "plan": "enterprise"}
    assert store.load("process:billing") == expected
    assert dict(pod_a.current) == expected and dict(pod_b.current) == expected


def test_a_seed_added_while_down_reaches_the_store_when_it_answers(asked_at_once):
    flaky = FlakyKeyValue()
    store = KeyValueContexts(flaky)
    flaky.down = True

    level = SharedValues(store, "process:billing", timedelta(0), seed={"beta": True})
    assert level.current["beta"] is True
    flaky.down = False
    level.current

    assert store.load("process:billing") == {"beta": True}


# --- across services ----------------------------------------------------------------------------


def test_a_context_handed_on_while_the_store_is_down_arrives_by_its_headers():
    flaky = FlakyKeyValue()
    store = KeyValueContexts(flaky)
    sent: list[dict[str, str]] = []
    read: list[Any] = []
    orders = UseFramework("outage-orders", log_after_execution=False)
    orders.store_context_in(store)

    @orders.feature(CommandWrite)
    class Publish(Feature):
        def execute(self, dto: CommandWrite) -> None:
            use_context().set("discount", 10)
            sent.append(context_propagation.inject(use_context()))

    billing = UseFramework("outage-billing", log_after_execution=False)
    billing.store_context_in(store)

    @billing.feature(CommandRead)
    class Bill(Feature):
        def execute(self, dto: CommandRead) -> None:
            read.append((use_context().get("tenant_id"), use_context().get("discount")))

    flaky.down = True
    with orders.context({"tenant_id": "acme"}):
        orders(CommandWrite())

    def consume() -> None:
        with billing.context(context_propagation.extract(sent[0]), kind=EntrypointKind.QUEUE):
            billing(CommandRead())

    thread = threading.Thread(target=consume)
    thread.start()
    thread.join(10)

    assert "x-context-node" not in sent[0]  # no id the store does not hold
    assert read == [("acme", 10)]


# --- what promises to persist -------------------------------------------------------------------


def test_what_promises_to_persist_still_fails_while_the_store_is_down():
    flaky = FlakyKeyValue()
    bus = UseFramework("outage-drafts", log_after_execution=False)
    bus.store_context_in(KeyValueContexts(flaky))
    flaky.down = True

    with bus.context({"tenant_id": "acme", "user_id": "ana"}):
        with pytest.raises(StoreUnavailable):
            use_context().drafts().save("invoice:1", {"total": 1}, 0, 0)


# --- said once ----------------------------------------------------------------------------------


def test_an_outage_is_said_once_when_it_starts_and_once_when_it_ends(asked_at_once):
    flaky = FlakyKeyValue()
    store = KeyValueContexts(flaky)
    level = SharedValues(store, "process:billing", timedelta(0))
    flaky.down = True

    with capture_logs() as logs:
        for _ in range(5):
            level.set("plan", "pro")
        flaky.down = False
        level.current
        level.current

    down = [log for log in logs if "did not answer" in log["event"]]
    back = [log for log in logs if "answers again" in log["event"]]
    assert len(down) == 1 and down[0]["log_level"] == "warning"
    assert len(back) == 1
