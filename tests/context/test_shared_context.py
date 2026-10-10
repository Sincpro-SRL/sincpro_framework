"""A bus that set a context store shares its context through it: the nodes of every execution are
written through, a flow handed on — a queue's headers, the remote bus — is read from the store by the
bus that receives it, and the global level is one for every service that shares the store.

    bus.context_store(store, ttl=timedelta(hours=24), every=timedelta(seconds=1))

Two services are two buses sharing one `InMemoryContexts` (Redis, in production). The receiving
side runs outside the sender's scope — in a new thread, which starts with no context of its own — so
what it reads comes from the headers and the store, never from the sender's tree in memory.
"""

import threading
from collections.abc import Callable, Iterator, Mapping
from datetime import timedelta
from typing import Any

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.context import InMemoryContexts, Level, use_context
from sincpro_framework.context.adapters.propagation import extract, inject
from sincpro_framework.context.domain.level import EntrypointKind
from tests.remote_execution.hosting import TRANSPORTS, host_over


class CommandPlaceOrder(DataTransferObject):
    pass


class CommandPublishOrder(DataTransferObject):
    pass


class CommandBill(DataTransferObject):
    overwrite_discount: int | None = None


class CommandReadTenant(DataTransferObject):
    who: str


class CommandReadGlobal(DataTransferObject):
    pass


class CommandPrice(DataTransferObject):
    pass


class ResponsePrice(DataTransferObject):
    seen: int | None


class CommandPriceOrder(DataTransferObject):
    pass


def _orders(store: InMemoryContexts, sent: list[dict[str, str]]) -> UseFramework:
    """Service A: places an order with a discount and hands it on to a queue."""
    orders = UseFramework("shared-context-orders", log_after_execution=False)
    orders.context_store(store)

    @orders.app_service(CommandPlaceOrder)
    class PlaceOrder(ApplicationService):
        def execute(self, dto: CommandPlaceOrder) -> None:
            self.context["discount"] = 10
            self.feature_bus.execute(CommandPublishOrder())

    @orders.feature(CommandPublishOrder)
    class PublishOrder(Feature):
        def execute(self, dto: CommandPublishOrder) -> None:
            sent.append(inject(use_context()))
            self.context["late"] = "yes"

    return orders


def _billing(store: InMemoryContexts, read: list[dict[str, Any]]) -> UseFramework:
    """Service B: bills what the queue brings, reading the context it was handed."""
    billing = UseFramework("shared-context-billing", log_after_execution=False)
    billing.context_store(store)

    @billing.feature(CommandBill)
    class Bill(Feature):
        def execute(self, dto: CommandBill) -> None:
            read.append(
                {"discount": self.context.get("discount"), "late": self.context.get("late")}
            )
            if dto.overwrite_discount is not None:
                self.context["discount"] = dto.overwrite_discount

    return billing


def _in_a_new_thread(work: Callable[[], object]) -> None:
    """`work` in a thread that starts with no context; what it raises is raised here."""
    failures: list[BaseException] = []

    def run() -> None:
        try:
            work()
        except BaseException as error:  # noqa: BLE001 — handed back to the test
            failures.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(10)
    if failures:
        raise failures[0]


def _received(
    billing: UseFramework, headers: Mapping[str, str], command: CommandBill
) -> None:
    """What a queue consumer of service B does with a message of service A."""

    def consume() -> None:
        with billing.context(extract(headers), kind=EntrypointKind.QUEUE):
            billing(command)

    _in_a_new_thread(consume)


# --- nothing set --------------------------------------------------------------------------------


def test_a_bus_without_a_context_store_keeps_nothing_and_works_as_always():
    store = InMemoryContexts()
    bus = UseFramework("shared-context-none", log_after_execution=False)
    seen: list[Any] = []

    @bus.feature(CommandReadGlobal)
    class ReadGlobal(Feature):
        def execute(self, dto: CommandReadGlobal) -> None:
            seen.append((self.context.get("tenant_id"), use_context().at(Level.GLOBAL)))

    with bus.context({"tenant_id": "acme"}):
        bus(CommandReadGlobal())

    assert seen == [("acme", None)]
    assert use_context().at(Level.GLOBAL) is None
    assert store.version("global") == 0


# --- one sharing bus ----------------------------------------------------------------------------


def test_two_concurrent_executions_on_a_sharing_bus_each_read_their_own_context():
    store = InMemoryContexts()
    bus = UseFramework("shared-context-concurrent", log_after_execution=False)
    bus.context_store(store)
    found: dict[str, tuple[Any, Any]] = {}
    barrier = threading.Barrier(2)

    @bus.feature(CommandReadTenant)
    class ReadTenant(Feature):
        def execute(self, dto: CommandReadTenant) -> None:
            self.context["stamp"] = dto.who
            barrier.wait(timeout=5)
            found[dto.who] = (self.context.get("tenant_id"), self.context.get("stamp"))

    def run(tenant_id: str) -> None:
        with bus.context({"tenant_id": tenant_id}):
            bus(CommandReadTenant(who=tenant_id))

    threads = [threading.Thread(target=run, args=(tenant,)) for tenant in ("acme", "beta")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert found == {"acme": ("acme", "acme"), "beta": ("beta", "beta")}


# --- handed on across a queue -------------------------------------------------------------------


def test_a_value_service_a_wrote_is_read_by_service_b_across_a_queue():
    store = InMemoryContexts()
    sent: list[dict[str, str]] = []
    read: list[dict[str, Any]] = []
    orders, billing = _orders(store, sent), _billing(store, read)

    with orders.context({"tenant_id": "acme"}):
        orders(CommandPlaceOrder())
    _received(billing, sent[0], CommandBill())

    assert read[0]["discount"] == 10


def test_a_value_service_a_wrote_after_the_hand_off_is_read_by_service_b():
    """`late` was written after the headers were made: only the store can bring it."""
    store = InMemoryContexts()
    sent: list[dict[str, str]] = []
    read: list[dict[str, Any]] = []
    orders, billing = _orders(store, sent), _billing(store, read)

    with orders.context({"tenant_id": "acme"}):
        orders(CommandPlaceOrder())
    _received(billing, sent[0], CommandBill())

    assert read[0]["late"] == "yes"


def test_only_an_id_travels_the_values_come_from_the_store():
    """Nothing but the identity and the node reach service B — the values are the store's."""
    store = InMemoryContexts()
    sent: list[dict[str, str]] = []
    read: list[dict[str, Any]] = []
    _orders(store, sent)(CommandPlaceOrder())
    only_ids = {
        name: value
        for name, value in sent[0].items()
        if name.startswith("x-")  # x-correlation-id, x-causation-id, x-context-node
    }

    _received(_billing(store, read), only_ids, CommandBill())

    assert "sincpro-context" not in only_ids and "x-context-node" in only_ids
    assert read[0]["discount"] == 10 and read[0]["late"] == "yes"


def test_a_receiver_that_did_not_set_the_store_reads_only_what_travelled():
    store = InMemoryContexts()
    sent: list[dict[str, str]] = []
    read: list[dict[str, Any]] = []
    _orders(store, sent)(CommandPlaceOrder())

    _received(_billing(InMemoryContexts(), read), sent[0], CommandBill())

    assert read[0]["discount"] == 10 and read[0].get("late") is None


def test_service_a_nodes_are_read_by_service_b_and_never_written():
    store = InMemoryContexts()
    sent: list[dict[str, str]] = []
    refused: list[Exception] = []
    _orders(store, sent)(CommandPlaceOrder())
    reader = UseFramework("shared-context-reader", log_after_execution=False)
    reader.context_store(store)

    @reader.feature(CommandBill)
    class Reach(Feature):
        def execute(self, dto: CommandBill) -> None:
            entrypoint = use_context().entrypoint
            sender = entrypoint.parent if entrypoint is not None else None
            assert sender is not None  # service A's Feature, read back
            try:
                sender.set("discount", 0)
            except TypeError as error:
                refused.append(error)

    _received(reader, sent[0], CommandBill())

    assert refused and "never written" in str(refused[0])


def test_what_service_b_writes_never_lands_on_service_a_nodes():
    store = InMemoryContexts()
    sent: list[dict[str, str]] = []
    read: list[dict[str, Any]] = []
    orders, billing = _orders(store, sent), _billing(store, read)

    with orders.context({"tenant_id": "acme"}):
        orders(CommandPlaceOrder())
    _received(billing, sent[0], CommandBill(overwrite_discount=99))
    _received(billing, sent[0], CommandBill())

    first, second = read
    assert first["discount"] == 10
    assert second["discount"] == 10


# --- handed on by the remote bus ----------------------------------------------------------------


def _pricing(store: InMemoryContexts) -> UseFramework:
    """Service B's `pricing` context, as every service builds it from the same code."""
    pricing = UseFramework("shared-context-pricing", log_after_execution=False)
    pricing.context_store(store)

    @pricing.feature(CommandPrice)
    class Price(Feature):
        def execute(self, dto: CommandPrice) -> ResponsePrice:
            return ResponsePrice(seen=self.context.get("discount"))

    return pricing


def _sales(store: InMemoryContexts, pricing: UseFramework) -> UseFramework:
    """Service A: writes a discount, then asks `pricing` — hosted elsewhere — for a price."""
    sales = UseFramework("shared-context-sales", log_after_execution=False)
    sales.context_store(store)
    sales.add_dependency("pricing", pricing)

    @sales.app_service(CommandPriceOrder)
    class PriceOrder(ApplicationService):
        pricing: UseFramework

        def execute(self, dto: CommandPriceOrder) -> ResponsePrice:
            self.context["discount"] = 10
            answer = self.pricing(CommandPrice(), ResponsePrice)
            assert answer is not None
            return answer

    return sales


@pytest.fixture(params=TRANSPORTS)
def remote_pricing(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[UseFramework, InMemoryContexts]]:
    """Service A's `pricing` — a reference to service B's, hosted over the transport — and the
    store both services share."""
    store = InMemoryContexts()
    for address in host_over(request.param, [_pricing(store)]):
        reference = _pricing(store)
        reference.hosted_by(f"{address}?timeout=5")
        yield reference, store


def test_a_value_service_a_wrote_is_read_by_the_context_it_calls_on_another_service(
    remote_pricing,
):
    pricing, store = remote_pricing
    sales = _sales(store, pricing)

    with sales.context({"tenant_id": "acme"}):
        answer = sales(CommandPriceOrder(), ResponsePrice)

    assert answer is not None and answer.seen == 10


# --- the process and the global level -----------------------------------------------------------


def test_the_process_level_is_kept_per_service():
    store = InMemoryContexts()
    bus = UseFramework("shared-context-process", log_after_execution=False)
    bus.context_store(store)

    use_context().root.set("flag", 1)

    kept = store.restore(f"process:{bus.observability.identity.service_name}")
    assert kept is not None and kept["flag"] == 1


def test_the_global_level_is_seen_by_every_sharing_bus():
    store = InMemoryContexts()
    read: list[Any] = []
    billing = UseFramework("shared-context-global-reader", log_after_execution=False)
    billing.context_store(store)

    @billing.feature(CommandReadGlobal)
    class ReadGlobal(Feature):
        def execute(self, dto: CommandReadGlobal) -> None:
            read.append(self.context.get("maintenance"))

    global_level = use_context().at(Level.GLOBAL)
    assert global_level is not None
    global_level.set("maintenance", True)
    _in_a_new_thread(lambda: billing(CommandReadGlobal()))

    assert read == [True]
    assert store.restore("global") == {"maintenance": True}


# --- what the store is given --------------------------------------------------------------------


class SpyContexts(InMemoryContexts):
    """An in-memory store that records how long each context was kept for."""

    def __init__(self) -> None:
        super().__init__()
        self.kept: list[tuple[str, timedelta | None]] = []

    def keep(self, key: str, values: Mapping[str, Any], ttl: timedelta | None = None) -> None:
        self.kept.append((key, ttl))
        super().keep(key, values, ttl)


def test_every_node_is_kept_for_the_ttl_the_bus_was_given():
    spy = SpyContexts()
    sent: list[dict[str, str]] = []
    orders = UseFramework("shared-context-ttl", log_after_execution=False)
    orders.context_store(spy, ttl=timedelta(minutes=5))

    @orders.feature(CommandPublishOrder)
    class PublishOrder(Feature):
        def execute(self, dto: CommandPublishOrder) -> None:
            self.context["discount"] = 10
            sent.append(inject(use_context()))

    with orders.context({"tenant_id": "acme"}):
        orders(CommandPublishOrder())

    nodes = [
        ttl for key, ttl in spy.kept if key != "global" and not key.startswith("process:")
    ]
    assert nodes, "no node of the execution was kept"
    assert set(nodes) == {timedelta(minutes=5)}


def test_over_redis_every_key_of_a_node_expires_nothing_grows_without_end():
    fakeredis = pytest.importorskip("fakeredis")
    from sincpro_framework.context import KeyValueContexts
    from sincpro_framework.data_layer.caching.adapters.redis import RedisKeyValue

    redis = fakeredis.FakeRedis()
    store = KeyValueContexts(RedisKeyValue(redis))
    sent: list[dict[str, str]] = []
    read: list[dict[str, Any]] = []
    _orders(store, sent)(CommandPlaceOrder())  # type: ignore[arg-type]
    _received(_billing(store, read), sent[0], CommandBill())  # type: ignore[arg-type]

    nodes = [key for key in redis.keys() if b":node:" in key]
    assert read[0]["discount"] == 10 and read[0]["late"] == "yes"
    assert nodes and all(0 < redis.ttl(key) <= 86400 for key in nodes)
