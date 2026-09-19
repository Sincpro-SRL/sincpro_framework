"""What the class's `translations()` says reaches the client through the definition.

A listing of ten thousand rows carries the definition once, and that definition has to be
enough to draw the screen: a label in every language the code wrote, the help beside it, the
values of a select. The class answers a dictionary, `Meta` maps it, and it is read once per
class.
"""

from dataclasses import dataclass, field
from enum import StrEnum

import pytest
from sqlalchemy import Column, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd.criteria import Condition, Criteria, Operator
from sincpro_framework.ddd.criteria.pagination import Pagination
from sincpro_framework.ddd.entity import Entity, Translated
from sincpro_framework.ddd.entity.model_meta import FieldType
from sincpro_framework.orm.sqlalchemy.custom_fields import TranslatedText
from sincpro_framework.orm.sqlalchemy.data_mapper import entity_table, map_aggregates
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.repository import Repository


class Status(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"


@dataclass
class Invoice(Entity):
    number: str = field(metadata={"label": {"default": "Number", "es": "Número"}})
    status: Status = field(
        default=Status.DRAFT,
        metadata={
            "label": {"default": "Status", "es": "Estado"},
            "help": {
                "default": "Where the invoice is in its life",
                "es": "En qué punto está",
            },
        },
    )
    title: dict[str, str] = field(
        default_factory=lambda: {"default": "untitled"},
        metadata={"label": {"default": "Title", "es": "Título"}},
    )

    @classmethod
    def translations(cls) -> Translated:
        return {"default": "Invoice", "es": "Factura"}


@dataclass
class Bare:
    """No translator at all: it is named after itself, and nothing else is said."""

    bare_id: str
    label: str = ""


own = registry()
invoice_table = entity_table(
    "invoice",
    own.metadata,
    Column("number", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("title", TranslatedText, nullable=False),
)
bare_table = Table(
    "bare", own.metadata, Column("bare_id", Text, primary_key=True), Column("label", Text)
)
map_aggregates(own, {Invoice: invoice_table})


@pytest.fixture(scope="module")
def invoices() -> Repository:
    database = Database("sqlite://")
    own.metadata.create_all(database.engine)
    engine = Repository(database)
    with engine.context() as repository:
        for one in (
            Invoice(
                number="F-1", status=Status.POSTED, title={"default": "Year", "es": "Año"}
            ),
            Invoice(number="F-2", title={"default": "Month", "es": "Mes"}),
        ):
            repository.save(one)
    return engine


def test_the_definition_carries_the_words_exactly_as_the_class_said_them():
    meta = describe(Invoice)

    assert meta.aggregate == "Invoice"
    assert meta.name == Invoice.translations()
    assert meta.fields["number"].label["es"] == "Número"
    assert meta.fields["status"].help["default"] == "Where the invoice is in its life"


def test_an_enum_field_publishes_its_values_as_choices():
    choices = describe(Invoice).fields["status"].choices

    assert choices == ["draft", "posted"]
    assert describe(Invoice).fields["status"].type is FieldType.TEXT


def test_the_definition_travels_as_plain_json():
    dumped = describe(Invoice).model_dump(mode="json")

    assert dumped["name"] == {"default": "Invoice", "es": "Factura"}
    assert dumped["fields"]["number"]["label"]["es"] == "Número"


def test_a_class_with_no_translator_is_named_after_itself():
    map_aggregates(own, {Bare: bare_table})

    assert describe(Bare).name == {"default": "Bare"}


def test_a_translated_column_round_trips_and_is_searched_in_every_language(invoices):
    stored = invoices.get(Invoice, invoices.fetch_all(Invoice).ids[0])
    assert stored is not None and stored.title["default"] in ("Year", "Month")

    found = invoices.search(
        Invoice,
        Criteria(
            where=Condition(field="title", operator=Operator.LIKE, value="año"),
            pagination=Pagination(limit=10),
        ),
    )

    assert [one.number for one in found] == ["F-1"]
    assert describe(Invoice).fields["title"].type is FieldType.TRANSLATED
    assert describe(Invoice).fields["title"].sortable is False
