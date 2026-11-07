"""The description of an entity is the same whichever store answers it.

Memory reads the class; SQL reads the class and the mapper. What both describe has to agree,
field by field, so a client — and a criteria checked against the description — sees one entity.
A store may add what only it knows (how a relation is kept: its keys); it never words a field
differently, and never disagrees on what a relation points at or whether a filter crosses it.
The models here are this module's own, mapped on their own registry, so no other module's mapping
is touched. One description serves both stores (the metamodel's `described`), so a value object
inside the record is described by memory too.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

import pytest
from sqlalchemy import Column, Date, Enum, ForeignKey, Numeric, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm import TableRelation, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.domain.custom_fields import TypedJson
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import (
    ModelIntrospection,
)
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.entity_meta import FieldType, Meta
from sincpro_framework.ddd.metamodel import contract_of
from sincpro_framework.ddd.multi_language import MultiLanguage


class Binding(StrEnum):
    HARD = "hard"
    SOFT = "soft"


@dataclass
class Size:
    width: int
    height: int


@dataclass
class Writer(Entity):
    name: str = field(metadata={"label": {"default": "Name", "es": "Nombre"}})
    books: list["Book"] = field(default_factory=list)


@dataclass
class Topic(Entity):
    label: str


@dataclass
class Book(Entity):
    title: str = field(metadata={"label": {"default": "Title"}})
    price: Decimal = Decimal("0")
    released_on: date | None = None
    binding: Binding = Binding.SOFT
    writer_id: str = ""
    size: Size | None = None
    writer: Writer | None = None
    topics: list[Topic] = field(default_factory=list)


shelf = registry()
writer_table = entity_table(
    "parity_writer", shelf.metadata, Column("name", Text, nullable=False)
)
topic_table = entity_table(
    "parity_topic", shelf.metadata, Column("label", Text, nullable=False)
)
book_table = entity_table(
    "parity_book",
    shelf.metadata,
    Column("title", Text, nullable=False),
    Column("price", Numeric(10, 2), nullable=False),
    Column("released_on", Date),
    Column("binding", Enum(Binding, native_enum=False), nullable=False),
    Column("writer_id", Text, ForeignKey("parity_writer.id"), nullable=False),
    Column("size", TypedJson(Size)),
)
book_topic = Table(
    "parity_book_topic",
    shelf.metadata,
    Column("book_id", Text, ForeignKey("parity_book.id"), primary_key=True),
    Column("topic_id", Text, ForeignKey("parity_topic.id"), primary_key=True),
)
map_aggregates(
    shelf,
    {Writer: writer_table, Topic: topic_table, Book: book_table},
    relations={
        Book: {
            "topics": TableRelation.many_to_many(
                Topic, through=book_topic, this_key="book_id", related_key="topic_id"
            )
        }
    },
)

RELATIONAL = {FieldType.MANY2ONE, FieldType.ONE2MANY, FieldType.MANY2MANY}
KEPT_BY_THE_STORE = {
    "identified_by",
    "parent_field",
    "related_field",
    "nullable",
    "type",
    "widget",
}
"""How a relation is kept — its keys, whether its key may be empty, a many-to-many's table, and so
the widget that edits it — is the store's to say: memory holds the records themselves. What it points at, how many, and whether a
filter crosses it are the model's, and agree."""
ENTITIES = [Writer, Topic, Book]


def described(entity: type) -> tuple[Meta, Meta]:
    return MemoryRepository().definition(entity), ModelIntrospection.describe(entity)


@pytest.mark.parametrize("entity", ENTITIES, ids=lambda one: one.__name__)
def test_the_entity_is_described_the_same_by_memory_and_by_sql(entity):
    memory, sql = described(entity)

    assert memory.model_dump(exclude={"fields"}) == sql.model_dump(exclude={"fields"})


@pytest.mark.parametrize("entity", ENTITIES, ids=lambda one: one.__name__)
def test_every_field_both_describe_is_described_the_same(entity):
    memory, sql = described(entity)

    differing = {
        name: {
            key: (value, sql.fields[name].model_dump()[key])
            for key, value in memory.fields[name].model_dump().items()
            if value != sql.fields[name].model_dump()[key]
            and not (memory.fields[name].type in RELATIONAL and key in KEPT_BY_THE_STORE)
        }
        for name in set(memory.fields) & set(sql.fields)
    }

    assert {name: diff for name, diff in differing.items() if diff} == {}


@pytest.mark.parametrize("entity", ENTITIES, ids=lambda one: one.__name__)
def test_what_only_sql_describes_is_how_a_relation_is_kept(entity):
    memory, sql = described(entity)

    only_sql = {
        name
        for name in set(sql.fields) - set(memory.fields)
        if sql.fields[name].type not in RELATIONAL
        and sql.fields[name].type is not FieldType.EMBEDDED
    }

    assert set(memory.fields) <= set(sql.fields)
    assert only_sql == set()


def test_an_embedded_value_object_is_described_by_memory_too():
    memory, sql = described(Book)

    embedded = {name for name, one in sql.fields.items() if one.type is FieldType.EMBEDDED}

    assert embedded and embedded <= set(memory.fields)


@pytest.mark.parametrize("entity", ENTITIES, ids=lambda one: one.__name__)
def test_every_label_and_help_is_a_multi_language_text_in_both(entity):
    for description in described(entity):
        for name, one in description.fields.items():
            assert isinstance(one.label, MultiLanguage), (description.aggregate, name)
            assert isinstance(one.help, MultiLanguage), (description.aggregate, name)


def test_the_published_contract_says_what_the_store_that_keeps_it_knows():
    ModelIntrospection.describe.cache_clear()
    ModelIntrospection.describe(Book)

    contract = contract_of(Book)
    topics = contract["properties"]["topics"]

    # A to-many holds a page of its records, as a reading answers it — never a bare list.
    assert topics["properties"]["items"] == {
        "type": "array",
        "items": {"$ref": "#/$defs/Topic"},
    }
    assert topics["properties"]["count"]["anyOf"][0] == {"$ref": "#/$defs/Count"}
    assert topics["required"] == ["items"]
    assert contract["$defs"]["Count"]["required"] == ["value", "exact"]
    assert {key: topics["x-sincpro"][key] for key in ("type", "widget", "navigable")} == {
        "type": "many2many",
        "widget": "many2many",
        "navigable": True,
    }
    assert contract["properties"]["price"]["type"] == "string"
    assert contract["$defs"]["Topic"]["x-sincpro"]["entity"] == "Topic"
