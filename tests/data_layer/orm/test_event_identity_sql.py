"""An event's `name` is its identity in its context's table: two classes never share one, and a
row whose name no class of this process carries is left out of every read and said out loud —
it never breaks the rows that can be read (PRD_29, G2 and G4)."""

import gc
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import Column, Text
from sqlalchemy.orm import registry
from structlog.testing import capture_logs

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.orm import (
    Repository,
    map_aggregates,
    map_events,
    template_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.services import event_mapping
from sincpro_framework.data_layer.orm.sqlalchemy.services.event_mapping import (
    map_new_event_classes,
)
from sincpro_framework.ddd import (
    Criteria,
    DomainEvent,
    DomainEvents,
    Entity,
    EntityReads,
    EventSourcedMixin,
    IRepository,
    ResponsePaginatedQuery,
)
from sincpro_framework.ddd.entity.entity_collection import Dropped
from sincpro_framework.ddd.exceptions import ContractViolation

from .engines import fresh


@dataclass(kw_only=True)
class StockEvent(DomainEvent):
    name = "tests.stock.v1.event"


@dataclass(kw_only=True)
class Received(StockEvent):
    name = "tests.stock.item.v1.received"
    units: int = 0


@dataclass
class Item(Entity):
    label: str = ""


@dataclass
class Bin(EventSourcedMixin, Entity):
    event_base = StockEvent
    units: int = 0

    def apply(self, event: DomainEvent) -> None:
        if isinstance(event, Received):
            self.units += event.units


stock = registry()
item_table = entity_table("ei_item", stock.metadata, Column("label", Text, nullable=False))
stock_events = template_table.event_table("ei_stock_events", stock.metadata)
map_aggregates(stock, {Item: item_table})
map_events(stock, StockEvent, stock_events)

VANISHED = "tests.stock.item.v1.vanished"


class ResponseItemEvents(ResponsePaginatedQuery):
    events: list[StockEvent]


class QueryItemEvents(DomainEvents[Item, ResponseItemEvents]):
    pass


@pytest.fixture
def repository(engine_url: str) -> Repository:
    return Repository(fresh(engine_url, stock.metadata))


def a_row_nobody_can_read(repository: Repository, entity_type: str, entity_id: str) -> str:
    """What a removed class, or a newer deployment sharing the table, leaves behind."""
    with repository.database.engine.begin() as connection:
        connection.execute(
            stock_events.insert().values(
                id="0-orphan",
                name=VANISHED,
                entity_type=entity_type,
                entity_id=entity_id,
                entity_version=99,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                payload={"units": 3},
            )
        )
    return "0-orphan"


def an_item_received(repository: IRepository) -> Item:
    item = Item(id="I-1", label="bolt")
    item.record(Received(units=5))
    repository.save(item)
    return item


# ── G2: one name, one class ─────────────────────────────────────────────────────────────


def test_two_classes_named_alike_are_refused_when_the_base_is_mapped():
    shared = registry()

    @dataclass(kw_only=True)
    class ClinicEvent(DomainEvent):
        name = "tests.clinic.v1.event"

    @dataclass(kw_only=True)
    class Admitted(ClinicEvent):
        name = "tests.clinic.v1.admitted"

    @dataclass(kw_only=True)
    class Readmitted(ClinicEvent):
        name = "tests.clinic.v1.admitted"

    with pytest.raises(ContractViolation) as refused:
        map_events(
            shared,
            ClinicEvent,
            template_table.event_table("ei_clinic_events", shared.metadata),
        )

    assert "Admitted" in str(refused.value)
    assert "Readmitted" in str(refused.value)
    assert "tests.clinic.v1.admitted" in str(refused.value)


def test_a_class_declared_later_under_a_taken_name_is_refused_before_it_is_mapped():
    shared = registry()

    @dataclass(kw_only=True)
    class FleetEvent(DomainEvent):
        name = "tests.fleet.v1.event"

    @dataclass(kw_only=True)
    class Departed(FleetEvent):
        name = "tests.fleet.v1.departed"

    map_events(
        shared, FleetEvent, template_table.event_table("ei_fleet_events", shared.metadata)
    )

    @dataclass(kw_only=True)
    class Left(FleetEvent):
        name = "tests.fleet.v1.departed"

    with pytest.raises(ContractViolation) as refused:
        map_new_event_classes()
    message = str(refused.value)
    del Left, refused  # until the class is gone, every mapping refuses: so is the process
    gc.collect()
    map_new_event_classes()

    assert "Departed and Left" in message
    assert "tests.fleet.v1.departed" in message


# ── G4: a row nobody can read never breaks a read ───────────────────────────────────────


def test_a_read_of_the_base_leaves_an_unknown_name_out_and_reports_it(
    repository, monkeypatch
):
    monkeypatch.setattr(event_mapping, "_warned", set())
    item = an_item_received(repository)
    a_row_nobody_can_read(repository, "Item", item.id)

    with capture_logs() as logs:
        page = repository.search(StockEvent, Criteria())
        repository.search(StockEvent, Criteria())

    assert [type(event) for event in page.items] == [Received]
    assert Dropped(field="name", reason="unknown_event") in page.dropped
    assert sum(VANISHED in line["event"] for line in logs) == 1
    assert repository.count(StockEvent, Criteria()).value == 1


def test_a_read_of_a_class_never_sees_the_unknown_row(repository):
    an_item_received(repository)
    a_row_nobody_can_read(repository, "Item", "I-1")

    page = repository.search(Received, Criteria())

    assert [event.units for event in page.items] == [5]
    assert page.dropped == ()


def test_getting_an_unknown_row_answers_none_and_a_known_one_still_answers(repository):
    an_item_received(repository)
    orphan = a_row_nobody_can_read(repository, "Item", "I-1")
    known = repository.search(Received, Criteria()).items[0]

    assert repository.get(StockEvent, orphan) is None
    found = repository.get(StockEvent, known.id)
    assert isinstance(found, Received) and found.units == 5


def test_an_entity_s_domain_events_leave_the_unknown_row_out(repository):
    item = an_item_received(repository)
    a_row_nobody_can_read(repository, "Item", item.id)
    bus = UseFramework("stock-events", log_after_execution=False)
    bus.add_dependency("repository", repository)

    @bus.feature(QueryItemEvents)
    class ItemReads(EntityReads[Item]):
        pass

    events = bus(QueryItemEvents(id=item.id), ResponseItemEvents).events

    assert [type(event) for event in events] == [Received]


def test_an_event_sourced_entity_with_an_unknown_event_is_refused_not_rebuilt_wrong(
    repository,
):
    bin_ = Bin(id="B-1")
    bin_.happened(Received(units=5))
    repository.save(bin_)
    assert repository.get(Bin, "B-1").units == 5

    a_row_nobody_can_read(repository, "Bin", "B-1")

    with pytest.raises(ContractViolation) as refused:
        repository.get(Bin, "B-1")
    assert "B-1" in str(refused.value)


# ── G12: the name is a column, never part of the payload ───────────────────────────────


def test_the_name_is_kept_in_its_column_and_left_out_of_the_payload(repository):
    an_item_received(repository)

    with repository.database.engine.connect() as connection:
        [(name, payload)] = connection.execute(
            stock_events.select().with_only_columns(
                stock_events.c.name, stock_events.c.payload
            )
        ).all()

    assert name == "tests.stock.item.v1.received"
    assert payload == {"units": 5}


# ── G17: a filter on what an event adds is answered alike in memory and in SQL ────────────


def test_a_filter_on_an_events_own_field_is_dropped_alike_in_memory_and_in_sql(repository):
    """What a subclass declares lives in the row's payload: both stores drop the filter as an
    unknown field and say so, instead of one applying it and the other widening."""
    from sincpro_framework.data_layer.repositories import MemoryRepository
    from sincpro_framework.ddd import Condition

    in_memory = MemoryRepository()
    for store in (repository, in_memory):
        an_item_received(store)
    asked = Criteria(where=Condition(field="units", value=99))

    answers = [store.search(Received, asked) for store in (repository, in_memory)]

    assert [[event.units for event in page.items] for page in answers] == [[5], [5]]
    assert [page.dropped for page in answers] == [
        (Dropped(field="units", reason="unknown_field"),)
    ] * 2
