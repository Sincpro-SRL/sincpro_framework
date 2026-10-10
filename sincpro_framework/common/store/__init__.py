"""The storage contracts every component shares, and their implementation in memory; engines that
need a technology (Redis, Memcached) live in `data_layer.caching`."""

from sincpro_framework.common.store.in_memory import InMemoryKeyValue
from sincpro_framework.common.store.key_value import KeyValueStore, StoreUnavailable

__all__ = ["InMemoryKeyValue", "KeyValueStore", "StoreUnavailable"]
