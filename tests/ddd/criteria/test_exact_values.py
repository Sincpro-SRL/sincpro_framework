"""A condition's value is read as the field's own type — a decimal exactly, a UUID as a UUID — so
the memory store answers what the SQL one answers, and neither misses a row for how a number
was written."""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Column, Numeric
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm import (
    Database,
    Repository,
    map_aggregates,
    template_table,
)
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Criteria, Entity
from sincpro_framework.ddd.entity.entity_meta import FieldType, describe_class


@dataclass
class Line(Entity):
    amount: Decimal = Decimal("0")


@dataclass
class Ticket:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    title: str = ""


MAPPING = registry()
map_aggregates(
    MAPPING,
    {
        Line: template_table.entity_table(
            "exact_line", MAPPING.metadata, Column("amount", Numeric(12, 2))
        )
    },
)


def _where(field_name: str, value: object) -> Criteria:
    return Criteria.model_validate(
        {"where": {"field": field_name, "operator": "=", "value": value}}
    )


def _stores(tmp_path: Path) -> list:
    database = Database(f"sqlite:///{tmp_path / 'exact.sqlite3'}")
    MAPPING.metadata.create_all(database.engine)
    return [MemoryRepository(), Repository(database)]


@pytest.mark.parametrize("written", ["0.1", "0.10", 0.1, Decimal("0.1")])
def test_a_decimal_is_found_however_its_value_was_written(tmp_path: Path, written: object):
    for repository in _stores(tmp_path):
        repository.save(Line(id="a", amount=Decimal("0.10")))
        repository.save(Line(id="b", amount=Decimal("2.50")))

        found = repository.search(Line, _where("amount", written)).items

        assert [one.id for one in found] == ["a"], type(repository).__name__


def test_a_decimal_field_says_it_is_exact():
    assert describe_class(Line).field("amount").exact


def test_a_uuid_identity_is_searched_ordered_and_filtered():
    repository = MemoryRepository()
    tickets = [Ticket(title=str(n)) for n in range(3)]
    for ticket in tickets:
        repository.save(ticket)

    everything = repository.search(Ticket, Criteria()).items
    one = repository.search(Ticket, _where("id", str(tickets[1].id))).items

    assert len(everything) == 3
    assert [ticket.title for ticket in one] == ["1"]
    assert describe_class(Ticket).field("id").type is FieldType.UUID


def test_uuid_pages_are_walked_with_the_cursor():
    repository = MemoryRepository()
    for n in range(5):
        repository.save(Ticket(title=str(n)))

    first = repository.search(Ticket, Criteria.model_validate({"pagination": {"limit": 3}}))
    rest = repository.search(
        Ticket,
        Criteria.model_validate({"pagination": {"limit": 3}}).resuming_from(first.cursor),
    )

    assert len(first.items) == 3 and len(rest.items) == 2
