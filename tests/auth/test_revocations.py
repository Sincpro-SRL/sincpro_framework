"""Revocations on a key-value store: the contract holds in memory and on Redis, an entry lives
only as long as what it covers, no subject or session lands in the store as written, and a store
that does not answer is refused or admitted as the project chose."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from sincpro_framework.auth.common.adapters.revocations import (
    InMemoryRevocations,
    KeyValueRevocations,
)
from sincpro_framework.auth.common.domain.revocations import Revocations, WhenUnavailable
from sincpro_framework.data_layer.store import InMemoryKeyValue, StoreUnavailable
from sincpro_framework.data_layer.store.adapters.redis import RedisKeyValue
from sincpro_framework.runtime.testing import ManualClock
from sincpro_framework.runtime.testing.contracts.revocations import RevocationsContract


class TestInMemoryRevocations(RevocationsContract):
    def make_revocations(self) -> Revocations:
        return InMemoryRevocations()


class TestRevocationsOnRedis(RevocationsContract):
    def make_revocations(self) -> Revocations:
        return KeyValueRevocations(RedisKeyValue(fakeredis.FakeRedis()))


def test_an_entry_lives_only_as_long_as_what_it_covers() -> None:
    clock = ManualClock(datetime(2026, 11, 20, tzinfo=UTC))
    revocations = KeyValueRevocations(InMemoryKeyValue(now=clock.now), now=clock.now)
    revocations.revoke_session("s-1", until=clock.now() + timedelta(minutes=10))
    assert revocations.state("user:1", session="s-1").session_revoked
    clock.advance(minutes=10)
    assert not revocations.state("user:1", session="s-1").session_revoked


def test_no_subject_or_session_is_stored_as_written() -> None:
    store = InMemoryKeyValue()
    revocations = KeyValueRevocations(store)
    revocations.revoke_session("sid-ana@acme.bo", datetime.now(UTC) + timedelta(hours=1))
    revocations.revoke_subject("ana@acme.bo", datetime.now(UTC) + timedelta(hours=1))
    revocations.bump_permissions("ana@acme.bo")
    stored = " ".join(store._values)  # what the store holds, read in a test
    assert "ana" not in stored and "acme" not in stored


class DownStore(InMemoryKeyValue):
    def get_many(self, keys: Sequence[str]) -> list[bytes | None]:
        raise StoreUnavailable("redis is down")


def test_a_store_that_does_not_answer_is_refused_by_default() -> None:
    with pytest.raises(StoreUnavailable):
        KeyValueRevocations(DownStore()).state("user:1", session="s-1")


def test_a_project_may_admit_while_its_store_is_down() -> None:
    revocations = KeyValueRevocations(DownStore(), on_unavailable=WhenUnavailable.ADMIT)
    assert revocations.state("user:1", session="s-1").refusal(None) is None
    assert revocations.state("user:1").permission_version == 0
