"""Every execution knows who it is, what caused it and which flow it belongs to (PRD_21 §2-§4).

The tree these tests build, and what each node must say:

    CommandConfirmSale   ApplicationService   the head: correlation = its own id
     ├─ CommandCheckCredit   Feature, same bus     caused by the ApplicationService
     ├─ CommandCheckCredit   Feature, same bus     a second execution, an id of its own
     └─ CommandReserve       Feature, another bus  caused by the ApplicationService too
"""

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.context import (
    CAUSATION_ID,
    CORRELATION_ID,
    EXECUTION_ID,
    Execution,
    Level,
    carrying,
)
from sincpro_framework.context.infrastructure.tree import current_execution
from sincpro_framework.ddd import DomainEvent
from sincpro_framework.event_driven import Publisher, Subscriber, SyncQueue
from sincpro_framework.exceptions import BusAlreadyBuilt
from sincpro_framework.observability.correlation import execution_context


class CommandConfirmSale(DataTransferObject):
    pass


class CommandCheckCredit(DataTransferObject):
    pass


class CommandReserve(DataTransferObject):
    pass


@dataclass(kw_only=True)
class SaleConfirmed(DomainEvent):
    name = "tests.identity.sale.v1.confirmed"


def _buses(seen: list[Execution]) -> tuple[UseFramework, UseFramework]:
    warehouse = UseFramework("identity-warehouse", log_after_execution=False)
    sales = UseFramework("identity-sales", log_after_execution=False)
    sales.add_dependency("warehouse", warehouse)

    @warehouse.feature(CommandReserve)
    class Reserve(Feature):
        def execute(self, dto: CommandReserve) -> None:
            seen.append(current_execution())  # type: ignore[arg-type]

    @sales.feature(CommandCheckCredit)
    class CheckCredit(Feature):
        def execute(self, dto: CommandCheckCredit) -> None:
            seen.append(current_execution())  # type: ignore[arg-type]

    @sales.app_service(CommandConfirmSale)
    class ConfirmSale(ApplicationService):
        def execute(self, dto: CommandConfirmSale) -> None:
            seen.append(current_execution())  # type: ignore[arg-type]
            self.feature_bus.execute(CommandCheckCredit())
            self.feature_bus.execute(CommandCheckCredit())
            self.warehouse(CommandReserve())  # type: ignore[attr-defined]

    return sales, warehouse


@pytest.fixture
def seen() -> list[Execution]:
    return []


def test_every_execution_is_one_with_its_parent_named(seen):
    sales, _ = _buses(seen)
    sales(CommandConfirmSale())

    head, first, second, reserve = seen
    assert (head.use_case, head.level, head.bus) == (
        "CommandConfirmSale",
        Level.APPLICATION,
        "identity-sales",
    )
    assert head.causation_id is None and head.correlation_id == head.execution_id
    assert (first.level, reserve.bus) == (Level.FEATURE, "identity-warehouse")
    assert len({one.execution_id for one in seen}) == 4
    for child in (first, second, reserve):
        assert child.causation_id == head.execution_id
        assert child.correlation_id == head.execution_id


def test_an_execution_id_is_a_uuid_v7_its_start_read_off_it(seen):
    sales, _ = _buses(seen)
    before = datetime.now(UTC) - timedelta(seconds=1)
    sales(CommandConfirmSale())

    head = seen[0]
    assert len(head.execution_id) == 32
    assert head.started_at is not None and head.started_at >= before


def test_outside_an_execution_there_is_none(seen):
    sales, _ = _buses(seen)
    sales(CommandConfirmSale())

    assert current_execution() is None


def test_two_calls_are_two_flows(seen):
    sales, _ = _buses(seen)
    sales(CommandCheckCredit())
    sales(CommandCheckCredit())

    assert seen[0].correlation_id != seen[1].correlation_id


# --- what is given wins -------------------------------------------------------------------------


def test_a_root_takes_the_identity_it_was_handed(seen):
    sales, _ = _buses(seen)
    handed = {CORRELATION_ID: "flow-7", CAUSATION_ID: "gateway-1", EXECUTION_ID: "req-42"}
    with sales.context(handed):
        sales(CommandConfirmSale())

    head, first = seen[0], seen[1]
    assert (head.execution_id, head.causation_id, head.correlation_id) == (
        "req-42",
        "gateway-1",
        "flow-7",
    )
    assert first.causation_id == "req-42" and first.correlation_id == "flow-7"
    assert first.execution_id != "req-42"


def test_a_correlation_alone_names_the_flow(seen):
    sales, _ = _buses(seen)
    with sales.context({CORRELATION_ID: "flow-from-a-header"}):
        sales(CommandCheckCredit())

    assert seen[0].causation_id is None
    assert seen[0].correlation_id == "flow-from-a-header"


def test_a_caller_beside_the_buses_hands_its_identity(seen):
    sales, _ = _buses(seen)
    with carrying({CORRELATION_ID: "cron-flow"}):
        sales(CommandCheckCredit())

    assert seen[0].correlation_id == "cron-flow"


def test_a_bus_mints_its_ids_with_the_generator_it_was_given(seen):
    sales, _ = _buses(seen)
    counter = iter(range(100))
    sales.execution_ids(lambda: f"sale-{next(counter)}")
    sales(CommandConfirmSale())

    assert [one.execution_id for one in seen[:3]] == ["sale-0", "sale-1", "sale-2"]
    assert len(seen[3].execution_id) == 32  # the warehouse keeps its own generator


def test_a_generator_given_after_the_build_is_refused(seen):
    sales, _ = _buses(seen)
    sales(CommandCheckCredit())

    with pytest.raises(BusAlreadyBuilt):
        sales.execution_ids(lambda: "late")


# --- across threads, tasks and events -----------------------------------------------------------


def test_threads_each_see_their_own_execution():
    bus = UseFramework("identity-threads", log_after_execution=False)
    found: dict[str, Execution] = {}
    barrier = threading.Barrier(4)

    class CommandWork(DataTransferObject):
        who: str

    @bus.feature(CommandWork)
    class Work(Feature):
        def execute(self, dto: CommandWork) -> None:
            barrier.wait(timeout=5)  # all four inside at once
            found[dto.who] = current_execution()  # type: ignore[assignment]

    threads = [
        threading.Thread(target=bus, args=(CommandWork(who=str(index)),))
        for index in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len({one.execution_id for one in found.values()}) == 4
    assert all(one.causation_id is None for one in found.values())


def test_an_async_caller_keeps_the_chain(seen):
    sales, _ = _buses(seen)
    asyncio.run(sales.get_async_bus()(CommandConfirmSale()))

    head, first = seen[0], seen[1]
    assert first.causation_id == head.execution_id


def test_an_execution_an_event_starts_is_caused_by_the_event():
    billing = UseFramework("identity-billing", log_after_execution=False)
    notifications = UseFramework("identity-notifications", log_after_execution=False)
    publisher = Publisher(SyncQueue(Subscriber(notifications)))
    published: list[DomainEvent] = []
    heard: list[Execution] = []

    @notifications.feature(SaleConfirmed)
    class Email(Feature):
        def execute(self, dto: SaleConfirmed) -> None:
            published.append(dto)
            heard.append(current_execution())  # type: ignore[arg-type]

    @billing.feature(CommandConfirmSale)
    class Confirm(Feature):
        def execute(self, dto: CommandConfirmSale) -> None:
            heard.append(current_execution())  # type: ignore[arg-type]
            publisher.publish(SaleConfirmed())

    billing(CommandConfirmSale())

    confirm, email = heard
    [event] = published
    assert event.causation_id == confirm.execution_id
    assert email.causation_id == event.id
    assert email.correlation_id == confirm.correlation_id


# --- what the signals say -----------------------------------------------------------------------


def test_every_signal_reads_the_executions_own_identity():
    """Logs, spans, GlitchTip and metrics read `execution_context()`: a nested execution says its
    own id there, never the one its root was handed."""
    bus = UseFramework("identity-signals", log_after_execution=False)
    said: list[tuple[dict, Execution]] = []

    @bus.feature(CommandCheckCredit)
    class Check(Feature):
        def execute(self, dto: CommandCheckCredit) -> None:
            said.append((dict(execution_context()), current_execution()))  # type: ignore[arg-type]

    @bus.app_service(CommandConfirmSale)
    class Confirm(ApplicationService):
        def execute(self, dto: CommandConfirmSale) -> None:
            self.feature_bus.execute(CommandCheckCredit())

    with bus.context({CORRELATION_ID: "flow-9", EXECUTION_ID: "req-1"}):
        bus(CommandConfirmSale())

    [(keys, check)] = said
    assert keys[EXECUTION_ID] == check.execution_id != "req-1"
    assert keys[CAUSATION_ID] == "req-1"
    assert keys[CORRELATION_ID] == "flow-9"
