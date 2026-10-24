"""Contexts inherit a core entity and redeclare an enum field with more values, in their context.

The core says an invoice is draft or posted. The SIAT context needs «observed»; its online variant,
inheriting from it, needs «contingency» too; an export context, beside SIAT, needs «shipped». None
can subclass the enum above it — Python refuses an enum that has members — so each writes its own,
a superset, and its subclass redeclares `state` with it:

    Invoice(State) ← InvoiceSiat(SiatState) ← InvoiceSiatOnline(OnlineState)     a chain
    Invoice(State) ← InvoiceExport(ExportState)                                  a sibling

Both ways a subclass extends an entity hold the same — its fields flat beside the core's, or its
answers delegated under its key — on every engine: the definition of each class says its own
values and words, the column stays text, and a query by any class reads what each context stored,
each row as the class it was saved as.
"""

from dataclasses import dataclass
from enum import StrEnum

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm import map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.ddd import Criteria
from sincpro_framework.ddd.criteria import Condition
from sincpro_framework.ddd.entity import Entity, Translated

from .engines import fresh


class State(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"

    @classmethod
    def translations(cls) -> Translated:
        return {"default": "State", "es": "Estado"}

    @classmethod
    def choice_translations(cls) -> dict[str, Translated]:
        return {cls.DRAFT: {"es": "Borrador"}, cls.POSTED: {"es": "Publicada"}}


class SiatState(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"
    OBSERVED = "observed"

    @classmethod
    def translations(cls) -> Translated:
        return {"default": "SIAT state", "es": "Estado SIAT"}

    @classmethod
    def choice_translations(cls) -> dict[str, Translated]:
        return {
            cls.DRAFT: {"es": "Borrador"},
            cls.POSTED: {"es": "Validada"},
            cls.OBSERVED: {"es": "Observada"},
        }


class OnlineState(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"
    OBSERVED = "observed"
    CONTINGENCY = "contingency"


class ExportState(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"
    SHIPPED = "shipped"


def installed(engine_url: str, delegated: bool):
    md = MetaData()
    reg = registry(metadata=md)

    @dataclass
    class Invoice(Entity):
        number: str = ""
        state: State = State.DRAFT

    @dataclass
    class InvoiceSiat(Invoice):
        cuf: str = ""
        state: SiatState = SiatState.DRAFT  # type: ignore[assignment]

    @dataclass
    class InvoiceSiatOnline(InvoiceSiat):
        cufd: str = ""
        state: OnlineState = OnlineState.DRAFT  # type: ignore[assignment]

    @dataclass
    class InvoiceExport(Invoice):
        incoterm: str = ""
        state: ExportState = ExportState.DRAFT  # type: ignore[assignment]

    def own(name: str, parent: str, column: str) -> Table:
        return Table(
            name,
            md,
            Column("id", Text, ForeignKey(f"{parent}.id"), primary_key=True),
            Column(column, Text),
        )

    map_aggregates(
        reg,
        {
            Invoice: entity_table(
                "invoice", md, Column("number", Text), Column("state", Text)
            ),
            InvoiceSiat: own("invoice_siat", "invoice", "cuf"),
            InvoiceSiatOnline: own("invoice_siat_online", "invoice_siat", "cufd"),
            InvoiceExport: own("invoice_export", "invoice", "incoterm"),
        },
        delegated=[Invoice] if delegated else [],
    )
    repository = Repository(fresh(engine_url, md))
    with repository.context() as unit:
        unit.save(Invoice(number="F-1", state=State.POSTED))
        unit.save(InvoiceSiat(number="F-2", cuf="C-2", state=SiatState.OBSERVED))
        unit.save(
            InvoiceSiatOnline(
                number="F-3", cuf="C-3", cufd="D-3", state=OnlineState.CONTINGENCY
            )
        )
        unit.save(InvoiceExport(number="F-4", incoterm="FOB", state=ExportState.SHIPPED))
    return (Invoice, InvoiceSiat, InvoiceSiatOnline, InvoiceExport), repository


@pytest.fixture(params=[False, True], ids=["flat", "delegated"])
def contexts(request, engine_url):
    return installed(engine_url, request.param)


def test_each_class_publishes_its_own_values_and_words(contexts):
    (Invoice, InvoiceSiat, InvoiceSiatOnline, InvoiceExport), _ = contexts

    assert describe(Invoice).fields["state"].choices == ["draft", "posted"]
    assert describe(InvoiceSiat).fields["state"].choices == ["draft", "posted", "observed"]
    assert describe(InvoiceSiatOnline).fields["state"].choices == [
        "draft",
        "posted",
        "observed",
        "contingency",
    ]
    assert describe(InvoiceExport).fields["state"].choices == ["draft", "posted", "shipped"]
    assert describe(Invoice).fields["state"].label["es"] == "Estado"
    assert describe(InvoiceSiat).fields["state"].label["es"] == "Estado SIAT"
    assert describe(InvoiceSiat).fields["state"].choice_labels["posted"] == {"es": "Validada"}
    assert describe(Invoice).fields["state"].choice_labels["posted"] == {"es": "Publicada"}


def test_a_query_by_each_class_reads_its_own_rows_and_those_below_it(contexts):
    (Invoice, InvoiceSiat, InvoiceSiatOnline, InvoiceExport), repository = contexts

    def read(entity: type) -> list[tuple[str, str, str]]:
        found = repository.search(entity, Criteria()).items
        return sorted((one.number, type(one).__name__, one.state) for one in found)

    assert read(Invoice) == [
        ("F-1", "Invoice", "posted"),
        ("F-2", "InvoiceSiat", "observed"),
        ("F-3", "InvoiceSiatOnline", "contingency"),
        ("F-4", "InvoiceExport", "shipped"),
    ]
    assert read(InvoiceSiat) == [
        ("F-2", "InvoiceSiat", "observed"),
        ("F-3", "InvoiceSiatOnline", "contingency"),
    ]
    assert read(InvoiceSiatOnline) == [("F-3", "InvoiceSiatOnline", "contingency")]
    assert read(InvoiceExport) == [("F-4", "InvoiceExport", "shipped")]


def test_the_core_filters_by_a_value_only_a_context_below_it_has(contexts):
    (Invoice, InvoiceSiat, InvoiceSiatOnline, InvoiceExport), repository = contexts

    def by_state(value: str) -> list[tuple[str, type]]:
        found = repository.search(
            Invoice, Criteria(where=Condition(field="state", value=value))
        ).items
        return [(one.number, type(one)) for one in found]

    assert by_state("observed") == [("F-2", InvoiceSiat)]
    assert by_state("contingency") == [("F-3", InvoiceSiatOnline)]
    assert by_state("shipped") == [("F-4", InvoiceExport)]


def test_a_record_read_by_the_core_comes_back_whole_as_the_deepest_class(contexts):
    (Invoice, InvoiceSiat, InvoiceSiatOnline, InvoiceExport), repository = contexts
    [online] = repository.search(InvoiceSiatOnline, Criteria()).items

    read = repository.get(Invoice, online.id)

    assert type(read) is InvoiceSiatOnline
    assert (read.cuf, read.cufd, read.state) == ("C-3", "D-3", OnlineState.CONTINGENCY)
