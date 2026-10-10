"""The in-memory store honours the whole `KeyValueStore` contract — the default a service and
its tests use until it needs a store its replicas share."""

import math
from datetime import UTC, datetime

from sincpro_framework.common.store import InMemoryKeyValue, KeyValueStore
from sincpro_framework.runtime.testing import KeyValueStoreContract, ManualClock


class TestInMemoryKeyValue(KeyValueStoreContract):
    def make_store(self) -> KeyValueStore:
        self.clock = ManualClock(datetime(2026, 9, 27, tzinfo=UTC))
        return InMemoryKeyValue(now=self.clock.now)

    def expire(self, store: KeyValueStore, seconds: float) -> None:
        self.clock.advance(seconds=math.ceil(seconds))
