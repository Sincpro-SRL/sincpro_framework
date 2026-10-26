"""The cache and the idempotency of a bounded context — and of the whole service — kept where the
context is kept, with nothing built.

What has to hold: `@billing.cache` computes once per its arguments and per whose the call is (the
context's tenant and user, nothing assumed), every strategy is a decorator and a tool by hand, a
bus keeps on its own store when it has one — else the process's, else memory —, replicas sharing a
store share the answer, bounded contexts sharing one never read each other's, an adapter keeps
through the facade it was handed, a store that fails never fails the call, `@billing.idempotency`
runs a write once across replicas and replays an answer the previous release kept, and a
duplicate in flight is refused at once.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import fakeredis
import pytest

import sincpro_framework
from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.context import (
    ContextStore,
    InMemoryContexts,
    KeyValueContexts,
    store_context_in,
    use_context,
)
from sincpro_framework.data_layer.caching import (
    CacheFacade,
    CachePolicy,
    FailSafe,
    Idempotency,
    IdempotencyPolicy,
    JsonCodec,
    QueryCachePolicy,
)
from sincpro_framework.data_layer.caching.domain import exceptions
from sincpro_framework.data_layer.caching.domain.freshness import Sliding, TimeToLive
from sincpro_framework.data_layer.caching.domain.validation import ExternalVersion
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.data_layer.store import KeyValueStore, RedisKeyValue
from sincpro_framework.ddd import Entity


def keys_in(server: fakeredis.FakeServer) -> list[str]:
    """Every key the shared Redis holds, as text."""
    held = fakeredis.FakeRedis(server=server).keys()
    return [one.decode() if isinstance(one, bytes) else str(one) for one in held]


def redis_on(server: fakeredis.FakeServer) -> RedisKeyValue:
    """A client of its own on the shared Redis, as each replica has."""
    return RedisKeyValue(fakeredis.FakeRedis(server=server))


def replica(name: str, server: fakeredis.FakeServer) -> UseFramework:
    """A bus whose context is kept on the Redis `server`."""
    bus = UseFramework(name, log_after_execution=False)
    bus.store_context_in(KeyValueContexts(redis_on(server)))
    return bus


class DownStore(KeyValueStore):
    """A shared store that does not answer."""

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        raise ConnectionError("the store is not answering")

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        raise ConnectionError("the store is not answering")

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        raise ConnectionError("the store is not answering")

    def increment(self, key: str, ttl: timedelta | None = None) -> int:
        raise ConnectionError("the store is not answering")

    def delete(self, key: str) -> None:
        raise ConnectionError("the store is not answering")

    def take(self, key: str) -> bytes | None:
        raise ConnectionError("the store is not answering")


class CommandRate(DataTransferObject):
    currency: str


# The cache: by default


def test_a_function_is_computed_once_per_arguments_with_nothing_built():
    billing = UseFramework("facade-default", log_after_execution=False)
    asked: list[str] = []

    @billing.cache
    def rate(currency: str) -> Decimal:
        asked.append(currency)
        return Decimal("6.96")

    assert rate("USD") == rate("USD") == Decimal("6.96")
    rate("EUR")

    assert asked == ["USD", "EUR"]


def test_the_default_keeps_a_minute_and_never_waits_long_for_another_caller():
    strategy = UseFramework("facade-defaults", log_after_execution=False).cache()

    assert strategy.policy.freshness == TimeToLive(
        ttl=timedelta(seconds=60), stale_for=timedelta(seconds=10)
    )
    assert strategy.policy.wait_for_others == timedelta(seconds=1)


def test_whose_an_answer_is_follows_the_context_and_nothing_is_assumed():
    billing = UseFramework("facade-owner", log_after_execution=False)
    asked: list[tuple[str | None, str | None]] = []

    @billing.cache
    def balance(customer: str) -> int:
        context = use_context()
        asked.append((context.get("tenant_id"), context.get("user_id")))
        return 1

    balance("c1")  # a service with no tenant and no user
    balance("c1")
    for tenant in ("acme", "globex", "acme"):
        with use_context().scoped({"tenant_id": tenant}):
            balance("c1")
    with use_context().scoped({"tenant_id": "acme", "user_id": "ana"}):
        balance("c1")

    assert asked == [(None, None), ("acme", None), ("globex", None), ("acme", "ana")]


def test_vary_by_adds_keys_of_the_projects_own():
    billing = UseFramework("facade-vary", log_after_execution=False)
    asked: list[str | None] = []

    @billing.cache(vary_by="language")
    def names(category: str) -> list[str]:
        asked.append(use_context().get("language"))
        return ["Producto"]

    for language in ("es", "en", "es"):
        with use_context().scoped({"language": language}):
            names("food")

    assert asked == ["es", "en"]


def test_a_methods_instance_never_splits_its_answer():
    billing = UseFramework("facade-method", log_after_execution=False)
    asked: list[str] = []

    class Siat:
        @billing.cache
        def products(self, tenant: str) -> list[str]:
            asked.append(tenant)
            return ["P-1"]

    Siat().products("acme")
    Siat().products("acme")

    assert asked == ["acme"]


def test_an_answer_is_let_go_of_by_its_arguments():
    billing = UseFramework("facade-forget", log_after_execution=False)
    asked: list[str] = []

    @billing.cache
    def rate(currency: str) -> Decimal:
        asked.append(currency)
        return Decimal("6.96")

    rate("USD")
    rate.forget("USD")  # type: ignore[attr-defined]
    rate("USD")

    assert asked == ["USD", "USD"]


def test_a_coroutine_is_kept_without_blocking_its_loop():
    billing = UseFramework("facade-coroutine", log_after_execution=False)
    asked: list[str] = []

    @billing.cache
    async def rate(currency: str) -> Decimal:
        await asyncio.sleep(0)
        asked.append(currency)
        return Decimal("6.96")

    async def twice() -> list[Decimal]:
        return [await rate("USD"), await rate("USD")]

    assert asyncio.run(twice()) == [Decimal("6.96")] * 2
    assert asked == ["USD"]


def test_a_generator_is_refused_when_decorated():
    billing = UseFramework("facade-generator", log_after_execution=False)

    with pytest.raises(exceptions.ProgrammingError, match="generator"):

        @billing.cache
        def rates():
            yield Decimal("6.96")


# The cache: by strategy


def test_each_strategy_says_its_policy_by_its_name():
    cache = UseFramework("facade-strategies", log_after_execution=False).cache

    def version() -> str:
        return "v1"

    ttl = cache.ttl(timedelta(minutes=5), stale_for=timedelta(seconds=30))
    sliding = cache.sliding(timedelta(minutes=30), timedelta(hours=8))
    versioned = cache.versioned(version, trusted_for=timedelta(seconds=5))
    safe = cache.ttl(timedelta(minutes=5), fail_safe=timedelta(hours=1))
    policy = CachePolicy[object](freshness=TimeToLive(ttl=timedelta(seconds=7)))

    assert ttl.policy.freshness.ttl == timedelta(minutes=5)  # type: ignore[union-attr]
    assert ttl.policy.freshness.stale_for == timedelta(seconds=30)  # type: ignore[union-attr]
    assert sliding.policy.freshness == Sliding(
        idle_for=timedelta(minutes=30), at_most=timedelta(hours=8)
    )
    assert isinstance(versioned.policy.validation, ExternalVersion)
    assert isinstance(safe.policy.failure, FailSafe)
    assert safe.policy.failure.last_resort_for == timedelta(hours=1)
    assert cache(policy).policy is policy  # anything `Cache` can do
    for strategy in (ttl, sliding, versioned, safe):
        assert strategy.policy.wait_for_others == timedelta(seconds=1)


def test_a_versioned_answer_is_computed_again_when_its_source_moves():
    billing = UseFramework("facade-versioned", log_after_execution=False)
    version = ["v1"]
    asked: list[str] = []

    @billing.cache.versioned(lambda: version[0])
    def catalog(tenant: str) -> list[str]:
        asked.append(version[0])
        return [f"product@{version[0]}"]

    assert catalog("acme") == catalog("acme") == ["product@v1"]
    version[0] = "v2"

    assert catalog("acme") == ["product@v2"]
    assert asked == ["v1", "v2"]


def test_a_strategy_is_a_tool_by_hand():
    billing = UseFramework("facade-by-hand", log_after_execution=False)
    products = billing.cache.ttl(timedelta(hours=1))
    asked: list[str] = []

    def fetch() -> list[str]:
        asked.append("soap")
        return ["P-1"]

    assert products.get(("products", "nit-1"), fetch) == ["P-1"]
    assert products.get(("products", "nit-1"), fetch) == ["P-1"]
    products.forget(("products", "nit-1"))
    products.get(("products", "nit-1"), fetch)

    assert asked == ["soap", "soap"]


def test_an_adapter_keeps_through_the_facade_it_was_handed():
    server = fakeredis.FakeServer()
    billing = replica("facade-adapter", server)
    asked: list[str] = []

    class SiatClient:
        """An adapter: it never imports the bus, it is handed the bounded context's cache."""

        def __init__(self, cache: CacheFacade) -> None:
            self.products = cache.ttl(timedelta(hours=1))

        def catalog(self, nit: str) -> list[str]:
            return self.products.get(
                ("catalog", nit), lambda: self.soap(nit), JsonCodec(list[str])
            )

        def soap(self, nit: str) -> list[str]:
            asked.append(nit)
            return ["P-1"]

    billing.add_dependency("siat", SiatClient(billing.cache))

    @billing.feature(CommandRate)
    class Catalog(Feature):
        siat: SiatClient

        def execute(self, dto: CommandRate) -> int:
            return len(self.siat.catalog(dto.currency))

    assert (
        billing(CommandRate(currency="nit-1")) == billing(CommandRate(currency="nit-1")) == 1
    )
    assert asked == ["nit-1"]
    assert any(key.startswith("cache:facade-adapter:") for key in keys_in(server))


# The cache: where it is kept


def test_replicas_sharing_the_context_store_share_the_answer():
    server = fakeredis.FakeServer()
    computed: list[str] = []
    answers = []
    for bus in (replica("facade-rates", server), replica("facade-rates", server)):

        @bus.cache
        def rate(currency: str) -> Decimal:
            computed.append(currency)
            return Decimal("6.96")

        @bus.feature(CommandRate)
        class Rate(Feature):
            def execute(self, dto: CommandRate) -> str:
                return str(rate(dto.currency))

        answers.append(bus(CommandRate(currency="USD")))

    assert answers == ["6.96", "6.96"]
    assert computed == ["USD"]  # the second replica read it from Redis
    assert any(key.startswith("cache:facade-rates:") for key in keys_in(server))


def test_bounded_contexts_sharing_one_store_never_read_each_others_answers():
    server = fakeredis.FakeServer()
    computed: list[str] = []

    def rate(currency: str) -> Decimal:
        computed.append(currency)
        return Decimal("6.96")

    for name in ("facade-billing", "facade-sales"):
        bus = replica(name, server)
        kept = bus.cache(rate)
        kept("USD")
        kept("USD")

    assert computed == ["USD", "USD"]  # one per bounded context
    held = keys_in(server)
    assert any(key.startswith("cache:facade-billing:") for key in held)
    assert any(key.startswith("cache:facade-sales:") for key in held)


def test_a_bounded_context_with_its_own_store_keeps_there_and_the_rest_in_the_process():
    own, shared = fakeredis.FakeServer(), fakeredis.FakeServer()
    store_context_in(redis_on(shared))
    billing = replica("facade-own", own)
    sales = UseFramework("facade-process", log_after_execution=False)

    @billing.cache
    def rate(currency: str) -> Decimal:
        return Decimal("6.96")

    @sales.cache
    def price(sku: str) -> Decimal:
        return Decimal("10")

    rate("USD")
    price("SKU-1")

    assert billing.store() is not sales.store()
    assert any(key.startswith("cache:facade-own:") for key in keys_in(own))
    assert not any("facade-own" in key for key in keys_in(shared))
    assert any(key.startswith("cache:facade-process:") for key in keys_in(shared))


def test_the_store_is_read_at_every_call_so_one_set_later_is_the_one_kept_on():
    server = fakeredis.FakeServer()
    billing = UseFramework("facade-later", log_after_execution=False)

    @billing.cache  # taken before the bus has a store of its own
    def rate(currency: str) -> Decimal:
        return Decimal("6.96")

    billing.store_context_in(KeyValueContexts(redis_on(server)))
    rate("USD")

    assert any(key.startswith("cache:facade-later:") for key in keys_in(server))


def test_the_global_cache_keeps_where_the_process_keeps_its_context():
    server = fakeredis.FakeServer()
    store_context_in(redis_on(server))
    asked: list[str] = []

    @sincpro_framework.cache.ttl(timedelta(minutes=5))
    def setting(name: str) -> str:
        asked.append(name)
        return "on"

    assert setting("feature-x") == setting("feature-x") == "on"
    assert asked == ["feature-x"]
    assert any(key.startswith("cache:global:") for key in keys_in(server))


def test_a_function_with_no_return_type_is_kept_in_this_process_on_a_shared_store():
    billing = replica("facade-untyped", fakeredis.FakeServer())
    asked: list[str] = []

    def untyped(currency):  # no return type: nothing to encode it by
        asked.append(currency)
        return {"rate": "6.96"}

    kept = billing.cache(untyped)

    assert kept("USD") == kept("USD") == {"rate": "6.96"}
    assert asked == ["USD"]


class ContextsBesideADownStore(InMemoryContexts):
    """The context answers; the store kept beside it — where the caches are — does not."""

    down = DownStore()

    def key_value_store(self) -> KeyValueStore:
        return self.down


def test_a_store_that_fails_never_fails_the_call():
    billing = UseFramework("facade-down", log_after_execution=False)
    billing.store_context_in(ContextsBesideADownStore())
    asked: list[str] = []

    @billing.cache
    def rate(currency: str) -> Decimal:
        asked.append(currency)
        return Decimal("6.96")

    assert rate("USD") == rate("USD") == Decimal("6.96")
    assert asked == ["USD"]  # bypassed, answered from this process


# The idempotency


class CommandCharge(DataTransferObject):
    request_id: str
    amount: int

    def idempotency_key(self) -> str:
        return self.request_id


class ResponseCharge(DataTransferObject):
    charge_id: str


def test_a_write_runs_once_across_replicas_sharing_the_context_store():
    server = fakeredis.FakeServer()
    charged: list[str] = []
    answers = []
    for bus in (replica("facade-once", server), replica("facade-once", server)):

        @bus.feature(CommandCharge)
        @bus.idempotency
        class Charge(Feature):
            def execute(self, dto: CommandCharge) -> ResponseCharge:
                charged.append(dto.request_id)
                return ResponseCharge(charge_id=f"ch-{len(charged)}")

        answers.append(bus(CommandCharge(request_id="r-1", amount=10), ResponseCharge))

    assert charged == ["r-1"]  # the second replica replayed the first answer from Redis
    assert answers[0] == answers[1]
    assert any(key.startswith("idempotency:facade-once:") for key in keys_in(server))


def test_the_idempotency_defaults_replay_a_day_and_never_wait():
    billing = UseFramework("facade-once-defaults", log_after_execution=False)

    @billing.idempotency
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> ResponseCharge:
            return ResponseCharge(charge_id="ch")

    @billing.idempotency(expires_after=timedelta(hours=1), vary_by="channel")
    class Refund(Feature):
        def execute(self, dto: CommandCharge) -> ResponseCharge:
            return ResponseCharge(charge_id="rf")

    policies = {
        name.rsplit(".", 1)[-1]: one for name, one in billing.idempotency.policies().items()
    }

    assert policies["Charge"] == IdempotencyPolicy(expires_after=timedelta(hours=24))
    assert policies["Charge"].wait_for_completion == timedelta(0)
    assert policies["Refund"].expires_after == timedelta(hours=1)
    assert policies["Refund"].vary_by == ("channel",)


def test_each_tenant_runs_its_own_write_with_the_same_request_key():
    bus = UseFramework("facade-once-tenants", log_after_execution=False)
    charged: list[str | None] = []

    @bus.feature(CommandCharge)
    @bus.idempotency
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> ResponseCharge:
            charged.append(use_context().get("tenant_id"))
            return ResponseCharge(charge_id="ch")

    for tenant in ("acme", "globex", "acme"):
        with bus.context({"tenant_id": tenant}):
            bus(CommandCharge(request_id="r-7", amount=10))

    assert charged == ["acme", "globex"]


def test_a_duplicate_in_flight_is_refused_at_once():
    billing = UseFramework("facade-in-flight", log_after_execution=False)
    policy = IdempotencyPolicy(expires_after=timedelta(minutes=5))
    codec = JsonCodec(ResponseCharge)

    def charge() -> ResponseCharge:
        billing.idempotency.run(
            "r-1", lambda: ResponseCharge(charge_id="twice"), policy, codec
        )
        return ResponseCharge(charge_id="once")

    with pytest.raises(exceptions.AlreadyInProgress):
        billing.idempotency.run("r-1", charge, policy, codec)


def test_an_answer_kept_by_the_previous_release_is_replayed():
    redis = RedisKeyValue(fakeredis.FakeRedis())
    previous = Idempotency(redis)  # how the previous release kept its records
    bus = UseFramework("facade-upgrade", log_after_execution=False)
    bus.store_context_in(KeyValueContexts(redis))
    charged: list[str] = []

    def charge(dto: CommandCharge) -> ResponseCharge:
        charged.append(dto.request_id)
        return ResponseCharge(charge_id="ch-before")

    dto = CommandCharge(request_id="r-8", amount=10)
    previous.run(  # what the previous release wrote: command, request, vary_by values
        ("facade-upgrade.CommandCharge", "r-8", {}),
        lambda: charge(dto),
        IdempotencyPolicy(expires_after=timedelta(minutes=5)),
        JsonCodec(ResponseCharge),
        payload=(dto, {}),
    )

    @bus.feature(CommandCharge)
    @bus.idempotency
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> ResponseCharge:
            return charge(dto)

    again = bus(CommandCharge(request_id="r-8", amount=10), ResponseCharge)

    assert again.charge_id == "ch-before" and charged == ["r-8"]  # replayed, not run again


def test_an_idempotency_of_its_own_still_keeps_where_it_was_told():
    elsewhere = RedisKeyValue(fakeredis.FakeRedis())
    own = Idempotency(elsewhere)
    bus = UseFramework("facade-once-own", log_after_execution=False)
    charged: list[str] = []

    @bus.feature(CommandCharge)
    @own.once(expires_after=timedelta(minutes=5))
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> ResponseCharge:
            charged.append(dto.request_id)
            return ResponseCharge(charge_id="ch-1")

    bus(CommandCharge(request_id="r-9", amount=10))
    bus(CommandCharge(request_id="r-9", amount=10))

    assert charged == ["r-9"]
    assert own.records.store is elsewhere  # type: ignore[attr-defined]


def test_the_global_idempotency_keeps_where_the_process_keeps_its_context():
    server = fakeredis.FakeServer()
    store_context_in(redis_on(server))
    bus = UseFramework("facade-once-global", log_after_execution=False)
    charged: list[str] = []

    @bus.feature(CommandCharge)
    @sincpro_framework.idempotency
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> ResponseCharge:
            charged.append(dto.request_id)
            return ResponseCharge(charge_id="ch")

    bus(CommandCharge(request_id="r-10", amount=10))
    bus(CommandCharge(request_id="r-10", amount=10))

    assert charged == ["r-10"]
    assert any(key.startswith("idempotency:global:") for key in keys_in(server))


# A Query's answer


@dataclass
class Invoice(Entity):
    customer_id: str = ""
    total: int = 0


class QueryTotal(DataTransferObject):
    customer_id: str


class ResponseTotal(DataTransferObject):
    total: int


def total_of(bus: UseFramework, computed: list[str]) -> MemoryRepository:
    repository = MemoryRepository()
    repository.save(Invoice(id="i1", customer_id="c1", total=100))
    bus.add_dependency("repository", repository)

    @bus.feature(QueryTotal)
    class Total(Feature):
        repository: MemoryRepository

        def execute(self, dto: QueryTotal) -> ResponseTotal:
            computed.append(dto.customer_id)
            invoice = self.repository.get(Invoice, "i1")
            assert invoice is not None
            return ResponseTotal(total=invoice.total)

    return repository


def test_a_querys_answer_is_kept_on_its_bounded_contexts_store_with_nothing_built():
    server = fakeredis.FakeServer()
    billing = replica("facade-queries", server)
    computed: list[str] = []
    total_of(billing, computed)

    caching = billing.cache.queries(QueryTotal)

    assert billing(QueryTotal(customer_id="c1"), ResponseTotal).total == 100
    assert billing(QueryTotal(customer_id="c1"), ResponseTotal).total == 100
    assert computed == ["c1"]
    assert caching is billing.cache.queries()  # one per bounded context
    assert caching.policies()["facade-queries.QueryTotal"].wait_for_others == timedelta(
        seconds=1
    )
    assert any(key.startswith("cache:facade-queries:query:") for key in keys_in(server))


def test_a_write_invalidates_on_the_bounded_contexts_store_from_any_context():
    server = fakeredis.FakeServer()
    billing = replica("facade-queries-written", server)
    computed: list[str] = []
    repository = total_of(billing, computed)
    caching = billing.cache.queries(QueryTotal, QueryCachePolicy(ttl=timedelta(minutes=5)))
    billing(QueryTotal(customer_id="c1"), ResponseTotal)

    invoice = repository.get(Invoice, "i1")
    assert invoice is not None
    invoice.total = 250
    repository.save(invoice)
    caching.invalidate(
        Invoice
    )  # the writer's context: no bus in play, the store is billing's

    assert billing(QueryTotal(customer_id="c1"), ResponseTotal).total == 250
    assert computed == ["c1", "c1"]


def test_the_global_cache_keeps_no_query():
    with pytest.raises(exceptions.ProgrammingError, match="bus.cache.queries"):
        sincpro_framework.cache.queries(QueryTotal)


# Where the context is kept


class ContextsOfItsOwn(ContextStore):
    """A project's own store: it keeps contexts, and stands on no `KeyValueStore`."""

    def __init__(self) -> None:
        self.kept: dict[str, dict] = {}

    def save(self, key: str, values, ttl: timedelta | None = None) -> None:
        self.kept[key] = dict(values)

    def load(self, key: str) -> dict | None:
        return self.kept.get(key)

    def delete(self, key: str) -> None:
        self.kept.pop(key, None)

    def version(self, key: str) -> int:
        return 0


def test_a_bus_keeps_on_its_own_store_else_the_processs_else_memory():
    bus = UseFramework("facade-order", log_after_execution=False)
    in_memory = bus.store()
    process = store_context_in(InMemoryContexts())
    own = KeyValueContexts(RedisKeyValue(fakeredis.FakeRedis()))

    assert isinstance(in_memory, InMemoryContexts) and in_memory is not process
    assert bus.store() is process
    bus.store_context_in(own)
    assert bus.store() is own


def test_keep_context_in_takes_a_key_value_store_or_a_context_store():
    redis = RedisKeyValue(fakeredis.FakeRedis())
    contexts = KeyValueContexts(redis, prefix="acme:context")

    wrapped = store_context_in(redis)
    assert isinstance(wrapped, KeyValueContexts) and wrapped.store is redis
    assert store_context_in(contexts) is contexts
    assert use_context().root.get(ContextStore) is contexts


def test_a_store_of_a_projects_own_with_no_key_value_store_keeps_in_this_process():
    billing = UseFramework("facade-own-kind", log_after_execution=False)
    billing.store_context_in(ContextsOfItsOwn())
    asked: list[str] = []

    @billing.cache
    def rate(currency: str) -> Decimal:
        asked.append(currency)
        return Decimal("6.96")

    assert rate("USD") == rate("USD")
    assert asked == ["USD"]  # said once in the log, never a failed call
