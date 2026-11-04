"""The rules of a sale order on a database: a broken one refuses the save before a row is written,
a sale read without its lines is not judged over lines it does not hold, the rows stored before a
rule existed are found, and a rule a database must count is a computed flag the check reads.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from sqlalchemy import Column, Date, ForeignKey, Integer, MetaData, Table, Text
from sqlalchemy.orm import registry
from structlog.testing import capture_logs

from sincpro_framework import UseFramework
from sincpro_framework.context import ContextKey
from sincpro_framework.data_layer.orm import Orphans, TableRelation, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.ddd import (
    Condition,
    CountMode,
    Criteria,
    Specification,
    check,
    computed,
)
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd import violations_of
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.runtime.testing.contracts.computed_fields import (
    ComputedFieldsContract,
)


def needs_delivery(sale: "Sale") -> bool:
    return sale.state == "confirmed" and sale.delivery is None


@dataclass
class SaleLine(Entity):
    sale_id: str = ""
    product: str = ""
    qty: int = 1

    @check(lambda line: line.qty)
    def sells_something(self) -> None:
        if self.qty < 1:
            raise ddd_exceptions.InvalidValue("A line sells at least one unit")


@dataclass
class Sale(Entity):
    customer: str = ""
    state: str = "draft"
    delivery: date | None = None
    lines: list[SaleLine] = field(default_factory=list)
    missing_delivery: bool = computed(
        sql=lambda t: (t.state == "confirmed") & t.delivery.is_(None),
        python=needs_delivery,
        default=False,
    )

    @check(lambda s: [s.delivery, s.state])
    def delivery_when_confirmed(self) -> None:
        if needs_delivery(self):
            raise ddd_exceptions.InvalidValue("A confirmed sale needs a delivery date")

    @check(lambda s: s.lines)
    def each_product_once(self) -> None:
        seen: set[str] = set()
        for position, line in enumerate(self.lines):
            if line.product in seen:
                raise ddd_exceptions.InvalidValue(
                    f"{line.product} is already on the sale", ("lines", position, "product")
                )
            seen.add(line.product)


sales = registry(metadata=MetaData())
sale_table = entity_table(
    "checked_sale",
    sales.metadata,
    Column("customer", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("delivery", Date, nullable=True),
)
line_table = entity_table(
    "checked_sale_line",
    sales.metadata,
    Column("sale_id", Text, ForeignKey("checked_sale.id"), nullable=False),
    Column("product", Text, nullable=False),
    Column("qty", Integer, nullable=False),
)
map_aggregates(
    sales,
    {Sale: sale_table, SaleLine: line_table},
    relations={
        Sale: {
            "lines": TableRelation.foreign_key(
                SaleLine, identified_by="sale_id", orphans=Orphans.DELETE
            )
        }
    },
)


def fresh_store() -> Repository:
    database = Database("sqlite://")
    sales.metadata.create_all(database.engine)
    return Repository(database)


@pytest.fixture
def store() -> Repository:
    return fresh_store()


def imported(store: Repository, *rows: Entity) -> None:
    with UseFramework("sales-import", log_after_execution=False).context(
        {ContextKey.SKIP_CHECKS: "rows of the old system"}
    ):
        for row in rows:
            store.save(row)


def test_a_broken_rule_refuses_the_save_before_a_row_is_written(store):
    sale = Sale(
        customer="acme",
        state="confirmed",
        lines=[SaleLine(product="desk"), SaleLine(product="desk", qty=0)],
    )

    with pytest.raises(ddd_exceptions.ChecksFailed) as refused:
        store.save(sale)

    assert [one.path for one in refused.value.failures] == [
        ("lines", 1, "qty"),
        ("delivery",),
        ("lines", 1, "product"),
    ]
    assert store.search(Sale, Criteria(count=CountMode.EXACT)).count.value == 0


def test_a_sale_read_without_its_lines_is_saved_without_judging_them(store):
    imported(store, Sale(customer="acme", lines=[SaleLine(product="desk")]))
    read = store.search(
        Sale, Criteria(specification=Specification.model_validate({"customer": {}}))
    ).items[0]
    read.customer = "acme ltd"

    with capture_logs() as logs:
        store.save(read)

    assert not [one for one in logs if one["log_level"] == "warning"]


def test_lines_held_in_part_are_not_judged_and_the_save_says_so(store):
    imported(
        store,
        Sale(customer="acme", lines=[SaleLine(product="desk"), SaleLine(product="desk")]),
    )
    only_one = Specification({"lines": Criteria(where=Condition(field="qty", value=1))})
    read = store.search(
        Sale,
        Criteria(specification=only_one, where=Condition(field="customer", value="acme")),
    ).items[0]
    read.lines = read.lines[:1]

    with capture_logs() as logs:
        store.save(read)

    warned = [one["event"] for one in logs if one["log_level"] == "warning"]
    assert any("each_product_once is not checked on save" in one for one in warned)


def test_the_rows_stored_before_a_rule_are_found(store):
    good = Sale(customer="a", state="confirmed", delivery=date(2026, 11, 30))
    late = Sale(customer="b", state="confirmed")
    doubled = Sale(customer="c", lines=[SaleLine(product="desk"), SaleLine(product="desk")])
    imported(store, good, late, doubled)

    assert [
        (one.record_id, one.path)
        for one in violations_of(store, Sale, Sale.delivery_when_confirmed)
    ] == [(late.id, ("delivery",))]
    assert [one.record_id for one in violations_of(store, Sale, Sale.each_product_once)] == [
        doubled.id
    ]


def test_violations_of_refuses_what_is_not_a_check_of_the_entity(store):
    with pytest.raises(ddd_exceptions.ProgrammingError, match="has no @check"):
        violations_of(store, Sale, needs_delivery)


def test_a_rule_the_database_counts_is_a_computed_flag_the_check_reads(store):
    imported(
        store,
        Sale(customer="a", state="confirmed"),
        Sale(customer="b", state="confirmed", delivery=date(2026, 11, 30)),
        Sale(customer="c", state="confirmed"),
    )

    flagged = store.search(
        Sale,
        Criteria(
            where=Condition(field="missing_delivery", value=True), count=CountMode.EXACT
        ),
    )

    assert flagged.count.value == 2


class TestTheFlagAgreesWithItsCheck(ComputedFieldsContract):
    entity = Sale

    def make_store(self) -> Any:
        store = fresh_store()
        imported(
            store,
            Sale(customer="a", state="confirmed"),
            Sale(customer="b", state="draft"),
            Sale(customer="c", state="confirmed", delivery=date(2026, 11, 30)),
        )
        return store


# ---------------------------------------------------------------------------------------------
# another aggregate it points at is not judged by this save
# ---------------------------------------------------------------------------------------------


@dataclass
class Tag(Entity):
    name: str = ""

    @check(lambda t: t.name)
    def lowercase(self) -> None:
        if self.name != self.name.lower():
            raise ddd_exceptions.InvalidValue("A tag is written in lowercase")


@dataclass
class Note(Entity):
    title: str = ""
    tags: list[Tag] = field(default_factory=list)

    @check(lambda n: n.tags)
    def at_most_three_tags(self) -> None:
        if len(self.tags) > 3:
            raise ddd_exceptions.InvalidValue("A note has at most three tags")

    @classmethod
    def DEFAULT_READING(cls) -> Specification:
        return Specification.model_validate({"tags": {}})


notes = registry(metadata=MetaData())
tag_table = entity_table("checked_tag", notes.metadata, Column("name", Text, nullable=False))
note_table = entity_table(
    "checked_note", notes.metadata, Column("title", Text, nullable=False)
)
note_tag = Table(
    "checked_note_tag",
    notes.metadata,
    Column("note_id", Text, ForeignKey("checked_note.id"), primary_key=True),
    Column("tag_id", Text, ForeignKey("checked_tag.id"), primary_key=True),
)
map_aggregates(
    notes,
    {Tag: tag_table, Note: note_table},
    relations={
        Note: {
            "tags": TableRelation.many_to_many(
                Tag, through=note_tag, this_key="note_id", related_key="tag_id"
            )
        }
    },
)


def test_a_record_saved_with_a_tag_that_breaks_the_tags_rule_is_not_refused_for_it():
    database = Database("sqlite://")
    notes.metadata.create_all(database.engine)
    store = Repository(database)
    legacy = Tag(name="URGENT")
    imported(store, legacy)
    read_tag = store.get(Tag, legacy.id)
    assert read_tag is not None

    note = Note(title="printer", tags=[read_tag])
    store.save(note)

    with pytest.raises(ddd_exceptions.ChecksFailed):
        store.save(Note(title="too many", tags=[read_tag] * 4))
