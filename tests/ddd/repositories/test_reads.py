"""What a unit of reading touched: every repository read notes its aggregate, so something above
the use case — a query cache — knows what an answer depends on without the use case saying so.

Both stores the framework ships note every read: search, get, count, exists, pluck — and the
SQL one notes every statement, relations and counts included, by the tables it reads.
"""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Column, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd import Criteria, Entity, MemoryRepository
from sincpro_framework.ddd.repositories.reads import aggregate_tag, noting_reads
from sincpro_framework.orm import Database, Repository, entity_table, map_aggregates


@dataclass
class Invoice(Entity):
    total: int = 0


@dataclass
class Customer(Entity):
    name: str = ""


MAPPING = registry()
map_aggregates(
    MAPPING,
    {
        Invoice: entity_table("reads_invoice", MAPPING.metadata, Column("total", Integer)),
        Customer: entity_table("reads_customer", MAPPING.metadata, Column("name", Text)),
    },
)


def test_nothing_is_noted_outside_a_unit_of_reading():
    repository = MemoryRepository()
    repository.save(Invoice(id="i1", total=10))

    repository.search(Invoice)

    with noting_reads() as reads:
        pass
    assert reads == set()


def test_every_read_of_the_memory_store_notes_its_aggregate():
    repository = MemoryRepository()
    repository.save(Invoice(id="i1", total=10))
    repository.save(Customer(id="c1", name="Ana"))

    with noting_reads() as searched:
        repository.search(Invoice)
    with noting_reads() as got:
        repository.get(Customer, "c1")
    with noting_reads() as counted:
        repository.count(Invoice)
        repository.exists(Customer)
        repository.pluck(Invoice, "total")

    assert searched == {aggregate_tag(Invoice)}
    assert got == {aggregate_tag(Customer)}
    assert counted == {aggregate_tag(Invoice), aggregate_tag(Customer)}


def test_every_statement_of_the_sql_store_notes_the_aggregates_of_its_tables(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'reads.sqlite3'}")
    MAPPING.metadata.create_all(database.engine)
    repository = Repository(database)
    repository.save(Invoice(id="i1", total=10))
    repository.save(Customer(id="c1", name="Ana"))

    with noting_reads() as searched:
        repository.search(Invoice, Criteria())
    with noting_reads() as counted:
        repository.count(Customer)
    with noting_reads() as got:
        repository.get(Invoice, "i1")

    assert searched == {aggregate_tag(Invoice)}
    assert counted == {aggregate_tag(Customer)}
    assert got == {aggregate_tag(Invoice)}


def test_units_of_reading_nest_and_the_outer_one_sees_everything():
    repository = MemoryRepository()
    repository.save(Invoice(id="i1", total=10))
    repository.save(Customer(id="c1", name="Ana"))

    with noting_reads() as outer:
        repository.search(Invoice)
        with noting_reads() as inner:
            repository.get(Customer, "c1")

    assert inner == {aggregate_tag(Customer)}
    assert outer == {aggregate_tag(Invoice), aggregate_tag(Customer)}
