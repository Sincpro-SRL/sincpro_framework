"""What a module says it needs from the core, checked where the module is mapped.

`docs/spec/extensibility.md` §8: the core publishes a contract version on an entity, a module
declares the one it was written against and the fields it reads, and the application does not
start when they no longer hold. A relation an extension installs also says its own cardinality.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, Numeric, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework import exceptions
from sincpro_framework.data_layer.orm import Relation, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.ddd import Criteria, Entity
from sincpro_framework.ddd.criteria import Specification

from .engines import fresh


def core(contract: str | None = "2.3"):
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class Move(Entity):
        number: str = ""
        total: Decimal = Decimal("0")
        CONTRACT: ClassVar[str | None] = contract

    move_t = entity_table("move", md, Column("number", Text), Column("total", Numeric(12, 2)))
    map_aggregates(reg, {Move: move_t})
    return md, reg, Move, move_t


def test_a_module_that_reads_a_field_the_core_has_is_mapped():
    md, reg, Move, _ = core()

    map_aggregates(reg, {}, uses={Move: ("number", "total")})


def test_a_module_that_reads_a_field_the_core_renamed_is_refused_naming_it():
    md, reg, Move, _ = core()

    with pytest.raises(
        exceptions.ProgrammingError, match=r"Move\.amount, which Move does not have"
    ):
        map_aggregates(reg, {}, uses={Move: ("amount",)})


def test_a_field_an_extension_added_is_not_one_the_core_has():
    md, reg, Move, _ = core()

    @dataclass
    class MoveDelivery(Move):
        route: str = ""

    delivery_t = Table(
        "move_delivery",
        md,
        Column("id", Text, ForeignKey("move.id"), primary_key=True),
        Column("route", Text),
    )
    map_aggregates(reg, {MoveDelivery: delivery_t})

    with pytest.raises(exceptions.ProgrammingError, match=r"Move\.route"):
        map_aggregates(reg, {}, uses={Move: ("route",)})


@pytest.mark.parametrize("wanted", ["2.3", "2.1", "2.0"])
def test_a_module_written_against_the_same_major_and_an_older_minor_is_mapped(wanted):
    _, reg, Move, _ = core("2.3")

    map_aggregates(reg, {}, requires={Move: wanted})


@pytest.mark.parametrize("published", ["3.0", "2.0", "1.9"])
def test_a_module_written_against_another_major_or_a_newer_minor_is_refused(published):
    _, reg, Move, _ = core(published)

    with pytest.raises(
        exceptions.ProgrammingError,
        match=rf"requires Move contract 2.1.*publishes {published}",
    ):
        map_aggregates(reg, {}, requires={Move: "2.1"})


def test_a_core_that_publishes_no_contract_cannot_be_required_one():
    _, reg, Move, _ = core(None)

    with pytest.raises(exceptions.ProgrammingError, match="publishes no CONTRACT version"):
        map_aggregates(reg, {}, requires={Move: "2.0"})


def test_a_relation_an_extension_installs_says_its_own_cardinality(engine_url):
    md, reg, Move, move_t = core()

    @dataclass
    class MoveFiscal(Entity):
        cufd: str = ""

    fiscal_t = entity_table("move_fiscal", md, Column("cufd", Text))
    map_aggregates(
        reg,
        {MoveFiscal: fiscal_t},
        relations={
            Move: {
                "fiscal": Relation.foreign_key(
                    MoveFiscal, parent_field="id", related_field="id", many=False
                )
            }
        },
    )
    store = Repository(fresh(engine_url, md))
    one = Move(id="M1", number="F-1")
    store.save(one)
    store.save(MoveFiscal(id="M1", cufd="C1"))

    [read] = list(
        store.search(
            Move,
            Criteria(
                specification=Specification({"number": Criteria(), "fiscal": Criteria()})
            ),
        )
    )

    assert read.fiscal.cufd == "C1"  # type: ignore[attr-defined]


def test_a_module_changes_how_a_field_of_the_core_is_shown_and_nothing_else():
    md, reg, Move, _ = core()

    map_aggregates(
        reg,
        {},
        overlay={Move: {"total": {"label": {"default": "Amount"}, "readonly": True}}},
    )

    total = describe(Move).fields["total"]
    assert (total.label, total.readonly, total.type) == (
        {"default": "Amount"},
        True,
        describe(Move).fields["total"].type,
    )
    assert describe(Move).fields["number"].readonly is False


def test_what_a_field_is_cannot_be_overlaid_by_a_module():
    md, reg, Move, _ = core()

    with pytest.raises(exceptions.ClientError, match="not type: that is the core's"):
        map_aggregates(reg, {}, overlay={Move: {"total": {"type": "text"}}})


def test_the_overlay_of_the_core_reaches_the_extensions_that_inherit_the_field():
    md, reg, Move, _ = core()

    @dataclass
    class MoveDelivery(Move):
        route: str = ""

    delivery_t = Table(
        "move_delivery",
        md,
        Column("id", Text, ForeignKey("move.id"), primary_key=True),
        Column("route", Text),
    )
    map_aggregates(
        reg, {MoveDelivery: delivery_t}, overlay={Move: {"total": {"required": True}}}
    )

    assert describe(MoveDelivery).fields["total"].required is True


# ── the kit a module runs in its own CI ────────────────────────────────────────

from sincpro_framework.runtime.testing.contracts.extension import (  # noqa: E402
    ExtensionContract,
)


def a_core_with_an_extension_and_a_store():
    md, reg, Move, _ = core("2.3")

    @dataclass
    class MoveDelivery(Move):
        route: str = ""

    delivery_t = Table(
        "move_delivery",
        md,
        Column("id", Text, ForeignKey("move.id"), primary_key=True),
        Column("route", Text),
    )
    map_aggregates(reg, {MoveDelivery: delivery_t})
    return Move, MoveDelivery, Repository(fresh("sqlite://", md))


CORE, EXTENSION, STORE = a_core_with_an_extension_and_a_store()


class TestMoveDeliveryExtension(ExtensionContract):
    entity, extension = CORE, EXTENSION
    uses = ("number", "total")
    requires = "2.1"

    def make_store(self):
        return STORE


class TestAModuleWrittenAgainstAFieldTheCoreRenamed(ExtensionContract):
    entity, extension = CORE, EXTENSION
    uses = ("amount",)

    def make_store(self):
        return STORE

    def test_every_field_the_module_reads_is_a_field_of_the_core(self):
        with pytest.raises(AssertionError, match="no longer has amount"):
            super().test_every_field_the_module_reads_is_a_field_of_the_core()


class TestAModuleWrittenAgainstAnotherMajor(ExtensionContract):
    entity, extension = CORE, EXTENSION
    requires = "3.0"

    def make_store(self):
        return STORE

    def test_the_core_publishes_a_contract_the_module_accepts(self):
        with pytest.raises(
            AssertionError, match="requires Move 3.0 and the core publishes 2.3"
        ):
            super().test_the_core_publishes_a_contract_the_module_accepts()
