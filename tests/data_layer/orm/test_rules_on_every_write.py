"""Every write keeps the aggregate's rules, or says out loud that it skips them.

A save judges and computes the whole aggregate. The doors past it write rows: an upsert judges and
computes each record over the row it writes, and refuses — before anything is written — a rule
its row cannot judge; a write by criteria holds no record, so it refuses every rule its values
reach; a write to the rows an aggregate owns refuses the owner's rules over them. One switch,
`ContextKey.SKIP_CHECKS` with a reason, lets any of them through, logged. Memory and SQL agree.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Text
from sqlalchemy.orm import registry
from structlog.testing import capture_logs

from sincpro_framework import UseFramework, exceptions
from sincpro_framework.context import ContextKey
from sincpro_framework.data_layer.orm import Orphans, TableRelation, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Condition, Criteria, check, compute
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd.entity import Entity

from .engines import ENGINES, fresh


@dataclass
class Rate(Entity):
    currency: str = ""
    per_usd: int = 1
    cents: int = 0
    state: str = "draft"
    approved_by: str | None = None
    note: str = ""
    label: str = ""

    @compute(lambda rate: rate.cents, depends=lambda rate: rate.per_usd)
    def cents_of(self) -> int:
        return self.per_usd * 100

    @compute(lambda rate: rate.label, depends=lambda rate: rate.currency, editable=True)
    def label_of(self) -> str:
        return self.currency.lower()

    @check(lambda rate: rate.per_usd)
    def positive_rate(self) -> None:
        if self.per_usd < 1:
            raise ddd_exceptions.InvalidValue("A rate is at least one")

    @check(lambda rate: [rate.approved_by, rate.state])
    def approved_when_published(self) -> None:
        if self.state == "published" and not self.approved_by:
            raise ddd_exceptions.InvalidValue("A published rate names who approved it")


@dataclass
class BatchLine(Entity):
    batch_id: str = ""
    qty: int = 1


@dataclass
class Batch(Entity):
    name: str = ""
    units: int = 0
    lines: list[BatchLine] = field(default_factory=list)

    @compute(lambda batch: batch.units, depends=lambda batch: batch.lines)
    def units_of(self) -> int:
        return sum(line.qty for line in self.lines)

    @check(lambda batch: batch.lines)
    def at_most_ten_units(self) -> None:
        if sum(line.qty for line in self.lines) > 10:
            raise ddd_exceptions.InvalidValue("A batch holds ten units at most")


rules = registry(metadata=MetaData())
map_aggregates(
    rules,
    {
        Rate: entity_table(
            "rule_rate",
            rules.metadata,
            Column("currency", Text, nullable=False, unique=True),
            Column("per_usd", Integer, nullable=False),
            Column("cents", Integer, nullable=False),
            Column("state", Text, nullable=False),
            Column("approved_by", Text, nullable=True),
            Column("note", Text, nullable=False),
            Column("label", Text, nullable=False),
        ),
        Batch: entity_table(
            "rule_batch",
            rules.metadata,
            Column("name", Text, nullable=False),
            Column("units", Integer, nullable=False),
        ),
        BatchLine: entity_table(
            "rule_batch_line",
            rules.metadata,
            Column("batch_id", Text, ForeignKey("rule_batch.id"), nullable=False),
            Column("qty", Integer, nullable=False),
        ),
    },
    relations={
        Batch: {
            "lines": TableRelation.foreign_key(
                BatchLine, identified_by="batch_id", orphans=Orphans.DELETE
            )
        }
    },
)

framework = UseFramework("rules-on-every-write", log_after_execution=False)


@contextmanager
def skipping(reason: str = "legacy import 2026-11") -> Iterator[str]:
    """A call that skips the rules. The skip is said once per execution, or per thread for the
    same reason outside one — so each call here gets a reason of its own."""
    said = f"{reason} #{uuid4().hex[:8]}"
    with framework.context({ContextKey.SKIP_CHECKS: said}):
        yield said


@pytest.fixture(params=["memory", *ENGINES])
def store(request: pytest.FixtureRequest) -> Any:
    if request.param == "memory":
        return MemoryRepository()
    return Repository(fresh(request.param, rules.metadata))


@pytest.fixture(params=ENGINES)
def sql(request: pytest.FixtureRequest) -> Repository:
    return Repository(fresh(request.param, rules.metadata))


def stored_usd(store: Any) -> Rate | None:
    found = store.search(Rate, Criteria(selection=Condition(field="currency", value="USD")))
    return found.items[0] if found.items else None


def usd(store: Any) -> Rate:
    found = stored_usd(store)
    assert found is not None, "no USD rate stored"
    return found


def warnings_in(logs: list[Any]) -> list[str]:
    return [one["event"] for one in logs if one["log_level"] == "warning"]


USD = Criteria(selection=Condition(field="currency", value="USD"))


# ── upsert: the row it writes is computed and judged ────────────────────────────────────


def test_an_upsert_refuses_a_record_that_breaks_a_rule_and_writes_nothing(store):
    with pytest.raises(ddd_exceptions.ChecksFailed) as refused:
        store.upsert(Rate(currency="USD", per_usd=0), on=("currency",))

    assert [one.path for one in refused.value.failures] == [("per_usd",)]
    assert stored_usd(store) is None


def test_an_upsert_computes_the_row_as_a_save_does(store):
    store.upsert(Rate(currency="USD", per_usd=7), on=("currency",))

    assert usd(store).cents == 700


def test_an_upsert_judges_the_overwrite_of_a_stored_row_too(store):
    store.upsert(Rate(currency="USD", per_usd=7), on=("currency",))

    with pytest.raises(ddd_exceptions.ChecksFailed):
        store.upsert(Rate(currency="USD", per_usd=7, state="published"), on=("currency",))

    assert usd(store).state == "draft"


def test_an_upsert_whose_conflict_would_mix_stored_and_handed_values_is_refused(store):
    with pytest.raises(exceptions.ProgrammingError) as refused:
        store.upsert(
            Rate(currency="USD", state="published", approved_by="ana"),
            on=("currency",),
            update=("state",),
        )

    said = str(refused.value)
    assert "Rate.approved_when_published watches approved_by" in said
    assert "a conflict keeps it as stored" in said
    assert "SKIP_CHECKS" in said
    assert stored_usd(store) is None


def test_an_upsert_that_overwrites_every_field_a_rule_reads_is_judged(store):
    store.upsert(Rate(currency="USD", per_usd=7), on=("currency",))

    store.upsert(
        Rate(currency="USD", per_usd=7, state="published", approved_by="ana"),
        on=("currency",),
        update=("state", "approved_by"),
    )
    with pytest.raises(ddd_exceptions.ChecksFailed):
        store.upsert(
            Rate(currency="USD", state="published"),
            on=("currency",),
            update=("state", "approved_by"),
        )

    assert (usd(store).state, usd(store).approved_by) == ("published", "ana")


def test_an_upsert_whose_conflict_writes_nothing_judges_what_an_insert_writes(store):
    with pytest.raises(ddd_exceptions.ChecksFailed):
        store.upsert(Rate(currency="USD", per_usd=0), on=("currency",), update=())

    store.upsert(Rate(currency="USD", per_usd=3), on=("currency",), update=())

    assert usd(store).cents == 300


def test_an_upsert_cannot_judge_a_rule_over_records_a_row_never_carries(store):
    with pytest.raises(exceptions.ProgrammingError) as refused:
        store.upsert(Batch(name="b-1"), on=("id",))

    assert "Batch.at_most_ten_units watches lines (the row does not carry it)" in str(
        refused.value
    )
    assert "Batch.units_of computes units from lines" in str(refused.value)


def test_an_upsert_never_drops_the_records_a_record_holds(store):
    with skipping(), pytest.raises(exceptions.ProgrammingError, match="Batch.lines holds 2"):
        store.upsert(
            Batch(name="b-1", lines=[BatchLine(qty=1), BatchLine(qty=2)]), on=("id",)
        )


# ── update_all: one statement judges no row ─────────────────────────────────────────────


def test_update_all_refuses_a_value_a_check_watches_and_changes_nothing(store):
    store.save(Rate(currency="USD", per_usd=7))

    with pytest.raises(exceptions.ProgrammingError) as refused:
        store.update_all(Rate, USD, {"state": "published"})

    assert "update_all of Rate cannot judge its rules" in str(refused.value)
    assert "Rate.approved_when_published watches state" in str(refused.value)
    assert usd(store).state == "draft"


def test_update_all_refuses_a_value_a_computation_reads(store):
    store.save(Rate(currency="USD", per_usd=7))

    with pytest.raises(exceptions.ProgrammingError, match="Rate.cents_of computes cents"):
        store.update_all(Rate, USD, {"per_usd": 8})


def test_update_all_refuses_a_computed_field_written_by_hand(store):
    store.save(Rate(currency="USD", per_usd=7))

    with pytest.raises(exceptions.ProgrammingError, match="Rate.cents_of computes cents"):
        store.update_all(Rate, USD, {"cents": 1})


def test_update_all_writes_what_no_rule_reads(store):
    store.save(Rate(currency="USD", per_usd=7))

    assert store.update_all(Rate, USD, {"note": "checked", "label": "dólar"}) == 1
    assert (usd(store).note, usd(store).label) == ("checked", "dólar")


# ── the switch: skipped out loud, never silently ────────────────────────────────────────


def test_skipping_the_checks_lets_an_upsert_write_a_broken_record_and_says_why(store):
    with capture_logs() as logs, skipping("rates of the old system") as reason:
        store.upsert(Rate(currency="USD", per_usd=0), on=("currency",))

    assert usd(store).per_usd == 0
    assert any(reason in one for one in warnings_in(logs))


def test_skipping_the_checks_still_computes_what_an_upsert_can(store):
    with skipping():
        store.upsert(Rate(currency="USD", per_usd=0), on=("currency",))

    assert usd(store).cents == 0
    with skipping():
        store.upsert(Rate(currency="USD", per_usd=4), on=("currency",))

    assert usd(store).cents == 400


def test_skipping_the_checks_lets_a_mixing_upsert_through_naming_what_it_left(store):
    with capture_logs() as logs, skipping("sync with the bank") as reason:
        store.upsert(
            Rate(currency="USD", state="published"), on=("currency",), update=("state",)
        )

    assert any(
        "Rate written by upsert past its rules" in one
        and "Rate.approved_when_published" in one
        and reason in one
        for one in warnings_in(logs)
    )


def test_skipping_the_checks_lets_update_all_through_and_names_what_goes_stale(store):
    store.save(Rate(currency="USD", per_usd=7))

    with capture_logs() as logs, skipping("nightly re-pricing") as reason:
        assert store.update_all(Rate, USD, {"per_usd": 9}) == 1

    assert (usd(store).per_usd, usd(store).cents) == (9, 700)
    assert any(
        "Rate written by update_all past its rules" in one
        and "Rate.cents_of computes cents from per_usd" in one
        and reason in one
        for one in warnings_in(logs)
    )


def test_the_skip_is_said_once_per_execution_not_once_per_row(store):
    with capture_logs() as logs, skipping("bulk load") as reason:
        for code in ("USD", "EUR", "BOB"):
            store.upsert(Rate(currency=code, per_usd=0), on=("currency",))

    assert len([one for one in warnings_in(logs) if reason in one]) == 1


# ── the rows an aggregate owns ──────────────────────────────────────────────────────────


def test_a_write_to_the_rows_an_aggregate_owns_refuses_the_owners_rules_over_them(sql):
    batch = Batch(name="b-1", lines=[BatchLine(qty=2)])
    sql.save(batch)
    lines = Criteria(selection=Condition(field="batch_id", value=batch.id))

    for write in (
        lambda: sql.update_all(BatchLine, lines, {"qty": 20}),
        lambda: sql.remove_all(BatchLine, lines),
        lambda: sql.upsert(BatchLine(batch_id=batch.id, qty=20), on=("id",)),
    ):
        with pytest.raises(exceptions.ProgrammingError) as refused:
            write()
        said = str(refused.value)
        assert "Batch.at_most_ten_units watches lines" in said
        assert "Batch.units_of computes units from lines" in said
        assert "the rows written are the lines of a Batch" in said

    assert sql.get(Batch, batch.id).units == 2


def test_skipping_the_checks_lets_a_write_reach_the_rows_an_aggregate_owns(sql):
    batch = Batch(name="b-1", lines=[BatchLine(qty=2)])
    sql.save(batch)

    with skipping("fix of a line typed wrong"):
        sql.update_all(
            BatchLine,
            Criteria(selection=Condition(field="batch_id", value=batch.id)),
            {"qty": 3},
        )

    assert [line.qty for line in sql.fetch_all(BatchLine, Criteria()).items] == [3]


def test_a_root_with_no_owner_is_removed_by_criteria_freely(store):
    store.save(Rate(currency="USD", per_usd=7))

    assert store.remove_all(Rate, USD) == 1


# ── a save, for the record ──────────────────────────────────────────────────────────────


def test_a_save_is_judged_whole_and_skipped_by_the_same_switch(store):
    with pytest.raises(ddd_exceptions.ChecksFailed):
        store.save(Batch(name="b-1", lines=[BatchLine(qty=11)]))

    with skipping():
        store.save(Batch(name="b-2", lines=[BatchLine(qty=11)]))
