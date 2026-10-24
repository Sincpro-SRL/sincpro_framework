"""The storage contracts every component shares, and their implementation in memory; engines that
need a technology (Redis, Memcached, DynamoDB) live in `data_layer.caching`. `Leases` — a name held
by one owner at a time across replicas — stands on any of them."""

from sincpro_framework.common.store.exceptions import StoreUnavailable
from sincpro_framework.common.store.in_memory import InMemoryKeyValue
from sincpro_framework.common.store.key_value import KeyValueStore
from sincpro_framework.common.store.leases import Lease, Leases, LeaseState

__all__ = [
    "InMemoryKeyValue",
    "KeyValueStore",
    "Lease",
    "LeaseState",
    "Leases",
    "StoreUnavailable",
]
