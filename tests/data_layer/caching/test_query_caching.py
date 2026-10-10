"""`QueryCaching`: a Query's answer kept by whoever composes the bounded context — the use case
knows nothing — and let go of when an aggregate it read is written.

The use case is an ApplicationService that reads two aggregates, as a real one does: a customer
by id and their invoices. It never mentions a cache.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    ProgrammingError,
    UseFramework,
)
from sincpro_framework.common.store import InMemoryKeyValue, KeyValueStore
from sincpro_framework.data_layer.caching import CachePolicy, QueryCaching
from sincpro_framework.data_layer.caching.adapters.redis import RedisKeyValue
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Criteria, Entity
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven import (
    Publisher,
    Subscriber,
    SyncQueue,
)
from sincpro_framework.runtime.testing import ManualClock


@dataclass
class Customer(Entity):
    name: str = ""


@dataclass
class Invoice(Entity):
    customer_id: str = ""
    total: int = 0


@dataclass(kw_only=True)
class InvoiceIssued(DomainEvent):
    name = "billing.v1.invoice_issued"
    total: int = 0


class QueryBalance(DataTransferObject):
    customer_id: str


class ResponseBalance(DataTransferObject):
    customer: str
    balance: int


class QueryPing(DataTransferObject):
    pass


class ResponsePing(DataTransferObject):
    pong: bool


def _billing(repository: MemoryRepository, executions: list[str]) -> UseFramework:
    billing = UseFramework("billing-cache", log_after_execution=False)
    billing.add_dependency("repository", repository)
    billing.add_dependency("executions", executions)

    @billing.app_service(QueryBalance)
    class Balance(ApplicationService):
        def execute(self, dto: QueryBalance) -> ResponseBalance:
            self.executions.append(dto.customer_id)
            time.sleep(0.01)
            customer = self.repository.get(Customer, dto.customer_id)
            invoices = self.repository.fetch_all(
                Invoice,
                Criteria.model_validate(
                    {
                        "where": {
                            "field": "customer_id",
                            "operator": "=",
                            "value": dto.customer_id,
                        }
                    }
                ),
            )
            return ResponseBalance(
                customer=customer.name if customer else "",
                balance=sum(invoice.total for invoice in invoices),
            )

    @billing.feature(QueryPing)
    class Ping(Feature):
        def execute(self, dto: QueryPing) -> ResponsePing:
            self.executions.append("ping")
            return ResponsePing(pong=True)

    return billing


STORES: dict[str, Callable[[ManualClock], KeyValueStore]] = {
    "in memory": lambda clock: InMemoryKeyValue(now=clock.now),
    "redis": lambda _clock: RedisKeyValue(fakeredis.FakeRedis()),
}


@dataclass
class World:
    billing: UseFramework
    caching: QueryCaching
    repository: MemoryRepository
    executions: list[str]
    clock: ManualClock
    store: KeyValueStore


def _world(store_name: str = "in memory", policy: CachePolicy | None = None) -> World:
    clock = ManualClock(datetime(2026, 9, 27, tzinfo=UTC))
    repository, executions = MemoryRepository(), []
    repository.save(Customer(id="c1", name="Ana"))
    repository.save(Invoice(id="i1", customer_id="c1", total=100))
    billing = _billing(repository, executions)
    store = STORES[store_name](clock)
    caching = QueryCaching(store, now=clock.now)
    caching.on(billing, QueryBalance, policy or CachePolicy(ttl=timedelta(minutes=5)))
    return World(billing, caching, repository, executions, clock, store)


def _balance(world: World, customer_id: str = "c1") -> ResponseBalance:
    return world.billing(QueryBalance(customer_id=customer_id), ResponseBalance)


@pytest.mark.parametrize("store_name", list(STORES))
def test_asking_again_is_answered_without_running_the_use_case(store_name):
    world = _world(store_name)

    first, again = _balance(world), _balance(world)

    assert first == again == ResponseBalance(customer="Ana", balance=100)
    assert world.executions == ["c1"]


def test_another_query_value_is_another_answer():
    world = _world()

    _balance(world, "c1")
    _balance(world, "c2")

    assert world.executions == ["c1", "c2"]


def test_writing_an_aggregate_the_answer_read_lets_it_go():
    world = _world()
    _balance(world)

    world.repository.save(Invoice(id="i2", customer_id="c1", total=50))
    world.caching.invalidate(Invoice)

    assert _balance(world).balance == 150
    assert world.executions == ["c1", "c1"]


def test_writing_an_aggregate_the_answer_never_read_keeps_it():
    world = _world()
    _balance(world)

    world.caching.invalidate(QueryPing)

    _balance(world)
    assert world.executions == ["c1"]


def test_what_the_answer_depends_on_is_what_it_read_nobody_declares_it():
    world = _world()
    _balance(world)

    assert world.caching.depends_on(QueryBalance(customer_id="c1")) == {
        f"{Customer.__module__}.Customer",
        f"{Invoice.__module__}.Invoice",
    }


def test_an_answer_that_read_nothing_is_not_kept_unless_its_dependencies_are_declared():
    world = _world()
    world.caching.on(world.billing, QueryPing, CachePolicy(ttl=timedelta(minutes=5)))

    world.billing(QueryPing(), ResponsePing)
    world.billing(QueryPing(), ResponsePing)

    assert world.executions == ["ping", "ping"]


def test_the_answer_expires_after_its_ttl():
    world = _world(policy=CachePolicy(ttl=timedelta(minutes=5), jitter=0, early_expiry=0))
    _balance(world)

    world.clock.advance(minutes=6)
    _balance(world)

    assert world.executions == ["c1", "c1"]


def test_each_tenant_has_its_own_answer():
    world = _world(policy=CachePolicy(ttl=timedelta(minutes=5), vary_by=("tenant_id",)))

    with world.billing.context({"tenant_id": "acme"}):
        _balance(world)
    with world.billing.context({"tenant_id": "globex"}):
        _balance(world)
    with world.billing.context({"tenant_id": "acme"}):
        _balance(world)

    assert world.executions == ["c1", "c1"]


def test_a_sensitive_context_key_the_policy_does_not_vary_by_is_refused():
    clock = ManualClock(datetime(2026, 9, 27, tzinfo=UTC))
    billing = _billing(MemoryRepository(), [])
    caching = QueryCaching(InMemoryKeyValue(now=clock.now), sensitive=("user_id",))
    caching.on(billing, QueryBalance, CachePolicy(ttl=timedelta(minutes=5)))

    with billing.context({"user_id": "u1"}), pytest.raises(ProgrammingError, match="user_id"):
        billing(QueryBalance(customer_id="c1"), ResponseBalance)


def test_a_stale_answer_is_served_while_another_replica_recomputes_it():
    world = _world(
        policy=CachePolicy(
            ttl=timedelta(minutes=5), stale_for=timedelta(minutes=5), jitter=0, early_expiry=0
        )
    )
    _balance(world)
    world.clock.advance(minutes=6)
    world.store.add(world.caching.lock_key(QueryBalance(customer_id="c1")), b"1")

    assert _balance(world).balance == 100
    assert world.executions == ["c1"]


def test_many_callers_missing_at_once_run_the_use_case_once():
    world = _world()
    answers: list[ResponseBalance] = []

    def ask() -> None:
        answers.append(_balance(world))

    callers = [threading.Thread(target=ask) for _ in range(8)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join()

    assert len(answers) == 8 and all(answer == answers[0] for answer in answers)
    assert world.executions == ["c1"]


def test_an_answer_about_to_expire_is_recomputed_early_once_in_a_while():
    clock = ManualClock(datetime(2026, 9, 27, tzinfo=UTC))
    repository, executions = MemoryRepository(), []
    repository.save(Customer(id="c1", name="Ana"))
    billing = _billing(repository, executions)
    caching = QueryCaching(
        InMemoryKeyValue(now=clock.now), now=clock.now, random=lambda: 1e-300
    )
    caching.on(billing, QueryBalance, CachePolicy(ttl=timedelta(minutes=5), jitter=0))

    billing(QueryBalance(customer_id="c1"), ResponseBalance)
    clock.advance(minutes=4, seconds=59)
    billing(QueryBalance(customer_id="c1"), ResponseBalance)

    assert executions == ["c1", "c1"]


def test_an_event_from_another_process_lets_go_of_what_depends_on_it():
    world = _world()
    _balance(world)
    listener = world.caching.invalidated_by({InvoiceIssued: [Invoice]})

    Publisher(SyncQueue(Subscriber(listener))).publish(InvoiceIssued(total=5))

    _balance(world)
    assert world.executions == ["c1", "c1"]


def test_a_query_without_a_declared_response_cannot_be_cached():
    billing = UseFramework("undeclared", log_after_execution=False)

    @billing.feature(QueryPing)
    class Ping(Feature):
        def execute(self, dto: QueryPing):  # noqa: ANN201 — the point: nothing declared
            return ResponsePing(pong=True)

    with pytest.raises(TypeError, match="declare"):
        QueryCaching(InMemoryKeyValue()).on(billing, QueryPing, CachePolicy(ttl=timedelta(1)))


def test_the_near_cache_answers_in_process_and_still_sees_an_invalidation():
    clock = ManualClock(datetime(2026, 9, 27, tzinfo=UTC))
    repository, executions = MemoryRepository(), []
    repository.save(Customer(id="c1", name="Ana"))
    billing = _billing(repository, executions)
    shared = InMemoryKeyValue(now=clock.now)
    caching = QueryCaching(shared, now=clock.now, near=timedelta(seconds=30))
    caching.on(billing, QueryBalance, CachePolicy(ttl=timedelta(minutes=5)))
    billing(QueryBalance(customer_id="c1"), ResponseBalance)

    QueryCaching(shared, now=clock.now).invalidate(Invoice)
    billing(QueryBalance(customer_id="c1"), ResponseBalance)

    assert executions == ["c1", "c1"]


def test_two_contexts_sharing_a_store_invalidate_only_their_own():
    clock = ManualClock(datetime(2026, 9, 27, tzinfo=UTC))
    shared = InMemoryKeyValue(now=clock.now)
    repository, first_runs, second_runs = MemoryRepository(), [], []
    repository.save(Customer(id="c1", name="Ana"))
    first, second = _billing(repository, first_runs), _billing(repository, second_runs)
    one = QueryCaching(shared, now=clock.now, namespace="billing-a")
    other = QueryCaching(shared, now=clock.now, namespace="billing-b")
    one.on(first, QueryBalance, CachePolicy(ttl=timedelta(minutes=5)))
    other.on(second, QueryBalance, CachePolicy(ttl=timedelta(minutes=5)))
    first(QueryBalance(customer_id="c1"), ResponseBalance)
    second(QueryBalance(customer_id="c1"), ResponseBalance)

    one.invalidate()
    one.invalidate(Invoice)
    first(QueryBalance(customer_id="c1"), ResponseBalance)
    second(QueryBalance(customer_id="c1"), ResponseBalance)

    assert first_runs == ["c1", "c1"] and second_runs == ["c1"]


def test_switched_off_every_query_runs_its_use_case_and_nothing_is_kept():
    world = _world()
    world.caching.enabled = False

    _balance(world)
    _balance(world)
    world.caching.enabled = True
    _balance(world)

    assert world.executions == ["c1", "c1", "c1"]


def test_the_caching_says_which_queries_it_keeps_and_how():
    world = _world(policy=CachePolicy(ttl=timedelta(minutes=5), vary_by=("tenant_id",)))

    assert world.caching.policies() == {
        "billing-cache.QueryBalance": CachePolicy(
            ttl=timedelta(minutes=5), vary_by=("tenant_id",)
        )
    }
