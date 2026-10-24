"""`TypedJson`: a value of the declared type in Python, JSON in the column — and a value changed
inside is written like one assigned, whatever column holds it.

    read      the type the column declares, never a dict           a model · a dataclass · a list
    write     validated, as JSON; None as NULL                     jsonb on Postgres
    changed   inside, without an assignment → written              a save · a commit of the session

The second half is not `TypedJson`'s: SQLAlchemy writes what was assigned and never looks inside a
value, so `shipment.address.city = "Sucre"` and a save answered and wrote nothing. The `Database`
keeps a copy of each such value it reads and compares before writing — `JsonText` and a JSON
column of the project's included.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

import pytest
from pydantic import BaseModel
from sqlalchemy import JSON, Column, select, text
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm import (
    JsonText,
    Repository,
    TypedJson,
    Writes,
    map_aggregates,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.ddd.entity import ChangeTrackingMixin, Entity, EntityUpdated

from .engines import fresh


class LineKind(StrEnum):
    GOODS = "goods"
    SERVICE = "service"


class Address(BaseModel):
    city: str
    street: str = ""


@dataclass
class Line:
    product: str
    quantity: Decimal
    kind: LineKind = LineKind.GOODS
    due_on: date | None = None


@dataclass
class Shipment(ChangeTrackingMixin, Entity):
    address: Address | None = None
    lines: list[Line] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


warehouse = registry()
shipment_table = entity_table(
    "shipment",
    warehouse.metadata,
    Column("address", TypedJson(Address)),
    Column("lines", TypedJson(list[Line]), nullable=False),
    Column("notes", JsonText, nullable=False),
    Column("tags", JSON, nullable=False),
)
map_aggregates(warehouse, {Shipment: shipment_table})


@pytest.fixture
def shipments(engine_url) -> Repository:
    return Repository(fresh(engine_url, warehouse.metadata))


def a_shipment() -> Shipment:
    return Shipment(
        address=Address(city="La Paz", street="Prado"),
        lines=[Line(product="P-1", quantity=Decimal("2.50"), due_on=date(2026, 10, 24))],
        notes={"door": "blue"},
        tags=["fragile"],
    )


def stored(shipments: Repository, shipment_id: str) -> Shipment:
    """What the table holds, read by a session that never saw the record."""
    found = shipments.get(Shipment, shipment_id)
    assert found is not None
    return found


# ---------------------------------------------------------------------------------------------
# The type, both ways
# ---------------------------------------------------------------------------------------------


def test_a_value_comes_back_as_its_type_never_as_a_dict(shipments):
    shipment = a_shipment()
    shipments.save(shipment)

    back = stored(shipments, shipment.id)

    assert back.address == Address(city="La Paz", street="Prado")
    assert back.lines == [
        Line(product="P-1", quantity=Decimal("2.50"), due_on=date(2026, 10, 24))
    ]
    assert isinstance(back.lines[0].quantity, Decimal)
    assert back.lines[0].kind is LineKind.GOODS


def test_the_column_holds_json_and_none_is_null(shipments):
    shipment = a_shipment()
    shipment.address = None
    shipments.save(shipment)

    with shipments.context() as unit:
        address, lines, is_null = unit.session.execute(
            text("SELECT address, lines, address IS NULL FROM shipment")
        ).one()

    assert address is None and is_null
    assert (json.loads(lines) if isinstance(lines, str) else lines) == [
        {"product": "P-1", "quantity": "2.50", "kind": "goods", "due_on": "2026-10-24"}
    ]


def test_a_value_that_is_not_the_type_is_refused_on_the_way_in(shipments):
    shipment = a_shipment()
    shipment.lines = [{"product": "P-1"}]  # type: ignore[list-item]

    with pytest.raises(StatementError, match="quantity"):
        shipments.save(shipment)


def test_a_dict_of_the_shape_is_written_and_read_back_as_the_type(shipments):
    shipment = a_shipment()
    shipment.address = {"city": "Sucre"}  # type: ignore[assignment]
    shipments.save(shipment)

    assert stored(shipments, shipment.id).address == Address(city="Sucre")


def test_a_value_is_compared_as_it_is_stored_and_reached_into_as_json(shipments):
    shipment = a_shipment()
    shipments.save(shipment)
    other = a_shipment()
    other.address = Address(city="Sucre")
    shipments.save(other)

    with shipments.context() as unit:
        address = shipment_table.c.address
        equal = unit.session.scalars(
            select(shipment_table.c.id).where(address == Address(city="Sucre"))
        ).all()
        inside = unit.session.scalars(
            select(shipment_table.c.id).where(address["city"].as_string() == "La Paz")
        ).all()

    assert equal == [other.id]
    assert inside == [shipment.id]


# ---------------------------------------------------------------------------------------------
# A value changed inside
# ---------------------------------------------------------------------------------------------


def test_a_field_changed_inside_a_model_is_written_by_a_save(shipments):
    shipment = a_shipment()
    shipments.save(shipment)

    with shipments.context() as unit:
        loaded = unit.get(Shipment, shipment.id)
        assert loaded is not None and loaded.address is not None
        loaded.address.city = "Sucre"
        unit.save(loaded)

    assert stored(shipments, shipment.id).address == Address(city="Sucre", street="Prado")


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        pytest.param(
            lambda one: one.lines.append(Line(product="P-2", quantity=Decimal(1))),
            lambda one: [line.product for line in one.lines] == ["P-1", "P-2"],
            id="an item added to a list of dataclasses",
        ),
        pytest.param(
            lambda one: setattr(one.lines[0], "quantity", Decimal("9")),
            lambda one: one.lines[0].quantity == Decimal("9"),
            id="a field of an item",
        ),
        pytest.param(
            lambda one: one.notes.update(door="red"),
            lambda one: one.notes == {"door": "red"},
            id="a key of a JsonText dict",
        ),
        pytest.param(
            lambda one: one.tags.append("cold"),
            lambda one: one.tags == ["fragile", "cold"],
            id="an item of a plain JSON column",
        ),
    ],
)
def test_every_column_that_holds_a_structure_writes_what_changed_inside(
    shipments, change, expected
):
    shipment = a_shipment()
    shipments.save(shipment)

    with shipments.context() as unit:
        loaded = unit.get(Shipment, shipment.id)
        change(loaded)
        unit.save(loaded)

    assert expected(stored(shipments, shipment.id))


def test_a_record_read_outside_a_unit_of_work_and_changed_inside_is_saved(shipments):
    """The shape of most Features: get, change, save — each its own session."""
    shipment = a_shipment()
    shipments.save(shipment)

    loaded = stored(shipments, shipment.id)
    assert loaded.address is not None
    loaded.address.city = "Cochabamba"
    shipments.save(loaded)

    assert stored(shipments, shipment.id).address == Address(
        city="Cochabamba", street="Prado"
    )


def test_the_change_inside_is_an_updated_event_with_its_value_before(shipments):
    shipment = a_shipment()
    shipments.save(shipment)

    with shipments.context() as unit:
        loaded = unit.get(Shipment, shipment.id)
        assert loaded is not None and loaded.address is not None
        loaded.address.city = "Sucre"
        unit.save(loaded)

    [event] = loaded.pull_events()
    assert isinstance(event, EntityUpdated)
    assert event.changes == {
        "address": (
            Address(city="La Paz", street="Prado"),
            Address(city="Sucre", street="Prado"),
        )
    }


def test_a_record_read_and_saved_untouched_records_nothing(shipments):
    shipment = a_shipment()
    shipments.save(shipment)

    with shipments.context() as unit:
        loaded = unit.get(Shipment, shipment.id)
        unit.save(loaded)

    assert loaded.pull_events() == []


def test_a_second_change_after_a_write_is_compared_with_what_was_written(shipments):
    shipment = a_shipment()
    shipments.save(shipment)

    with shipments.context() as unit:
        loaded = unit.get(Shipment, shipment.id)
        assert loaded is not None and loaded.address is not None
        loaded.address.city = "Sucre"
        unit.save(loaded)
        loaded.address.city = "Tarija"
        unit.save(loaded)

    assert stored(shipments, shipment.id).address == Address(city="Tarija", street="Prado")
    assert [event.changes["address"][1].city for event in loaded.pull_events()] == [
        "Sucre",
        "Tarija",
    ]


def test_a_record_created_then_changed_inside_in_the_same_unit_is_written(shipments):
    with shipments.context() as unit:
        shipment = a_shipment()
        unit.save(shipment)
        assert shipment.address is not None
        shipment.address.city = "Oruro"
        unit.save(shipment)

    assert stored(shipments, shipment.id).address == Address(city="Oruro", street="Prado")


def test_a_value_read_again_is_the_one_a_later_change_is_compared_with(shipments):
    shipment = a_shipment()
    shipments.save(shipment)

    with shipments.context() as unit:
        loaded = unit.get(Shipment, shipment.id)
        unit.session.refresh(loaded)
        assert loaded is not None and loaded.address is not None
        loaded.address.city = "Potosí"
        unit.save(loaded)

    assert stored(shipments, shipment.id).address == Address(city="Potosí", street="Prado")


def test_a_change_inside_never_saved_is_put_back_as_one_assigned_is(shipments):
    """`Writes.SAVED`: a write is a save — a value changed inside is no exception."""
    shipment = a_shipment()
    shipments.save(shipment)

    with shipments.context() as unit:
        loaded = unit.get(Shipment, shipment.id)
        assert loaded is not None and loaded.address is not None
        loaded.address.city = "Sucre"

    assert stored(shipments, shipment.id).address == Address(city="La Paz", street="Prado")
    assert loaded.address == Address(city="La Paz", street="Prado")


def test_writes_changed_commits_a_change_inside_with_no_save(shipments):
    shipment = a_shipment()
    shipments.save(shipment)

    with shipments.context(writes=Writes.CHANGED) as unit:
        loaded = unit.get(Shipment, shipment.id)
        loaded.lines[0].quantity = Decimal("7")

    assert stored(shipments, shipment.id).lines[0].quantity == Decimal("7")


def test_a_plain_session_of_the_database_commits_a_change_inside(shipments):
    shipment = a_shipment()
    shipments.save(shipment)

    with shipments.database.session() as session:
        loaded = session.get(Shipment, shipment.id)
        assert loaded is not None
        loaded.tags.append("cold")

    assert stored(shipments, shipment.id).tags == ["fragile", "cold"]
