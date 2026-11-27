"""Isolation lives in the execution context, so it goes wherever the context goes in this
process — a nested use case, a hook, a thread handed the context, an async call — and never
crosses to another: the tenant travels, the isolation is declared where the code runs.
"""

import asyncio
from dataclasses import dataclass
from decimal import Decimal

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.context import ContextExecutor, context_propagation, context_scopes
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity, Hook, Hooks
from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.repositories.data_filters import DataFilterState
from sincpro_framework.entrypoints.adapters.queue.wire import queue_context
from sincpro_framework.event_driven.domain.envelope import Envelope
from sincpro_framework.tenancy import Tenancy, TenantOwnedMixin


@dataclass
class Invoice(TenantOwnedMixin, Entity):
    total: Decimal = Decimal("0")


class ResponseCount(DataTransferObject):
    value: int


class CommandCountInThread(DataTransferObject):
    pass


class CommandSave(DataTransferObject):
    pass


class CommandCount(DataTransferObject):
    pass


def _store(*tenants: str) -> MemoryRepository:
    return MemoryRepository(*(Invoice(total=Decimal(1), tenant_id=one) for one in tenants))


def test_a_hook_runs_inside_the_isolation_of_its_use_case():
    tenancy = Tenancy()
    bus = UseFramework("tenancy-hooks", log_after_execution=False)
    hooks = Hooks(None).inject(bus)
    seen: list[int] = []
    store = MemoryRepository(Invoice(tenant_id="globex"), hooks=hooks)

    @hooks.on(Invoice)
    class CountsSiblings(Hook):
        def before_save(self, record: Invoice) -> None:
            seen.append(store.count(Invoice, Criteria()).value)

    tenancy.on(bus)

    @bus.feature(CommandSave)
    @tenancy.isolated
    class Save(Feature):
        def execute(self, dto: CommandSave) -> None:
            store.save(Invoice(total=Decimal(1)))

    with bus.context({"tenant_id": "acme"}):
        bus(CommandSave())
    assert seen == [0]  # the hook saw acme's only, not globex's


def test_a_thread_handed_the_context_inherits_the_isolation():
    tenancy = Tenancy()
    bus = UseFramework("tenancy-threads", log_after_execution=False)
    store = _store("acme", "globex", "globex")
    tenancy.on(bus)

    @bus.feature(CommandCountInThread)
    @tenancy.isolated
    class CountInThread(Feature):
        def execute(self, dto: CommandCountInThread) -> ResponseCount:
            with ContextExecutor(max_workers=1) as pool:
                counted = pool.submit(lambda: store.count(Invoice, Criteria()).value).result()
            return ResponseCount(value=counted)

    with bus.context({"tenant_id": "globex"}):
        assert bus(CommandCountInThread(), ResponseCount).value == 2


def test_an_async_call_runs_isolated_in_its_tenant():
    tenancy = Tenancy()
    bus = UseFramework("tenancy-aio", log_after_execution=False)
    store = _store("acme", "globex", "globex")
    tenancy.on(bus)

    @bus.feature(CommandCount)
    @tenancy.isolated
    class Count(Feature):
        def execute(self, dto: CommandCount) -> ResponseCount:
            return ResponseCount(value=store.count(Invoice, Criteria()).value)

    async def both() -> list[int]:
        async_bus = bus.get_async_bus()

        async def one(tenant: str) -> int:
            with bus.context({"tenant_id": tenant}):
                answered = await async_bus(CommandCount(), ResponseCount)
            return answered.value

        return list(await asyncio.gather(one("acme"), one("globex")))

    assert asyncio.run(both()) == [1, 2]


def test_the_tenant_travels_and_the_isolation_does_not():
    """What leaves the process — a remote bus, a queue, a broker — carries the tenant; whether
    the receiver isolates is its own use case's declaration, never a value it is sent."""
    tenancy = Tenancy()
    with tenancy.with_tenant("acme"), tenancy.isolated():
        assert DataFilterState.current().applied  # isolated here
        carried = context_propagation.extract(
            context_propagation.inject(context_scopes.flow())
        )
    assert carried.get("tenant_id") == "acme"
    assert all(not isinstance(key, type) for key in carried)


@dataclass
class InvoicePaid(DomainEvent):
    tenant_id: str | None = None


def test_an_event_delivered_out_of_any_request_still_says_its_tenant():
    headers = Envelope.headers_for(
        InvoicePaid(entity_id="F-1", tenant_id="acme"), source="billing"
    )
    assert context_propagation.extract(headers)["tenant_id"] == "acme"


def test_a_queue_reads_the_tenant_only_as_every_wire_carries_it():
    assert "tenant_id" not in queue_context(Envelope(), {"x-tenant": "acme"})
    carried = context_propagation.inject({"tenant_id": "acme"})
    assert queue_context(Envelope(), carried)["tenant_id"] == "acme"


def test_an_event_goes_out_as_its_tenant_s_whatever_flow_publishes_it():
    """A support session working in A writes B's record: the fact it recorded is B's."""
    tenancy = Tenancy()
    with tenancy.with_tenants(["acme", "globex"], "acme"):
        headers = Envelope.headers_for(InvoicePaid(entity_id="F-1", tenant_id="globex"))
    carried = context_propagation.extract(headers)
    assert carried["tenant_id"] == "globex"
    assert "tenant_ids" not in carried
