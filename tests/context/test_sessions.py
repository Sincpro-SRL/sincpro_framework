"""Sessions: one sign-in, and a client that keeps using it is never asked again; one abandoned
lapses on its own; a code used once is used once."""

from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.context import KeyValueContexts, Sessions, use_context
from sincpro_framework.data_layer.store import (
    InMemoryKeyValue,
    KeyValueStore,
    StoreUnavailable,
)
from sincpro_framework.runtime.testing import ManualClock


def _sessions(
    idle_for: timedelta | None = timedelta(minutes=30), at_most: timedelta | None = None
) -> tuple[Sessions, ManualClock]:
    clock = ManualClock(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    store = InMemoryKeyValue(now=clock.now)
    return Sessions(store, idle_for, at_most, now=clock.now), clock


def test_a_session_opened_is_read_back_by_its_id():
    sessions, _ = _sessions()

    opened = sessions.open({"user_id": "ana", "token": "t0k"})
    found = sessions.get(opened.id)

    assert found is not None and found.values == {"user_id": "ana", "token": "t0k"}
    assert found.opened_at == opened.opened_at and len(opened.id) >= 40


def test_a_session_in_use_never_lapses_and_an_abandoned_one_does():
    sessions, clock = _sessions(idle_for=timedelta(minutes=30))
    opened = sessions.open({"user_id": "ana"})

    for _ in range(10):  # five hours, used every twenty minutes
        clock.advance(minutes=20)
        assert sessions.get(opened.id) is not None
    clock.advance(minutes=31)

    assert sessions.get(opened.id) is None


def test_a_session_with_no_idle_limit_lasts_until_it_is_closed():
    sessions, clock = _sessions(idle_for=None)
    opened = sessions.open({"user_id": "ana"})

    clock.advance(days=400)
    found = sessions.get(opened.id)
    sessions.close(opened.id)

    assert found is not None and found.expires_at is None
    assert sessions.get(opened.id) is None


def test_at_most_caps_a_session_however_much_it_is_used():
    sessions, clock = _sessions(idle_for=timedelta(hours=1), at_most=timedelta(hours=2))
    opened = sessions.open({"user_id": "ana"})

    clock.advance(minutes=50)
    assert sessions.get(opened.id) is not None
    clock.advance(minutes=50)
    held = sessions.get(opened.id)
    clock.advance(minutes=21)

    assert held is not None and held.expires_at == opened.opened_at + timedelta(hours=2)
    assert sessions.get(opened.id) is None


def test_a_saved_session_keeps_its_id_and_opening_and_a_closed_one_is_never_brought_back():
    sessions, clock = _sessions()
    opened = sessions.open({"user_id": "ana"})
    clock.advance(minutes=5)

    saved = sessions.save(opened.id, {"user_id": "ana", "company": 3})
    sessions.close(opened.id)

    assert saved is not None and saved.opened_at == opened.opened_at
    assert saved.values == {"user_id": "ana", "company": 3}
    assert sessions.save(opened.id, {"user_id": "eve"}) is None
    assert sessions.get(opened.id) is None


def test_a_code_taken_is_taken_once():
    sessions, _ = _sessions(idle_for=timedelta(minutes=5))
    code = sessions.open({"client_id": "claude", "redirect_uri": "https://claude.ai/cb"})

    first = sessions.take(code.id)

    assert first is not None and first.values["client_id"] == "claude"
    assert sessions.take(code.id) is None


def test_every_replica_on_the_store_reads_the_same_session():
    clock = ManualClock(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    shared = InMemoryKeyValue(now=clock.now)
    pod_a = Sessions(shared, now=clock.now)
    pod_b = Sessions(shared, now=clock.now)

    opened = pod_a.open({"user_id": "ana"})
    pod_b.close(opened.id)

    assert pod_a.get(opened.id) is None


def test_the_context_answers_the_sessions_of_the_store_it_is_saved_on():
    store = InMemoryKeyValue()
    bus = UseFramework("sessions-on-the-bus", log_after_execution=False)
    bus.store_context_in(KeyValueContexts(store))

    with bus.context({"tenant_id": "acme"}):
        opened = use_context().sessions(idle_for=timedelta(days=30)).open({"user_id": "ana"})
        found = use_context().sessions().get(opened.id)

    assert found is not None and store.get(f"session:sessions:{opened.id}") is not None


class DownKeyValue(InMemoryKeyValue):
    """A store that does not answer."""

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        raise StoreUnavailable("DownKeyValue did not answer")

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        raise StoreUnavailable("DownKeyValue did not answer")


def test_a_session_cannot_pretend_to_be_known_while_the_store_is_down():
    store: KeyValueStore = DownKeyValue()
    sessions = Sessions(store)

    with pytest.raises(StoreUnavailable):
        sessions.open({"user_id": "ana"})
    with pytest.raises(StoreUnavailable):
        sessions.get("9fK")
