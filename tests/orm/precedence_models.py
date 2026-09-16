"""The cases where a name could be read two ways, so the precedence has something to decide.

notes: list[str]            a column with a list annotation → a field, never a relation
author / editor             two foreign keys to the same table: one inferred by `<name>_id`,
                            the other declared
reviewer                    a third pointer with no column named after it → nothing inferred
dimensions                  a pydantic value object in a JSON column, deserialised on read
drafts: list[Draft]         a dataclass with no table and no declaration → ignored
chapters                    a one2many inferred from `chapter.manuscript_id`
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import registry
from sqlalchemy.types import TypeDecorator

from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity_collection import EntityCollection
from sincpro_framework.orm.sqlalchemy.custom_fields import JsonText
from sincpro_framework.orm.sqlalchemy.data_mapper import (
    Relation,
    entity_table,
    map_aggregates,
)


class Dimensions(BaseModel):
    """A value object as a pydantic model, stored as JSON."""

    width: int
    height: int
    unit: str = "cm"


class EmbeddedDimensions(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else value.model_dump_json()

    def process_result_value(self, value: str | None, dialect: Dialect) -> Any:
        return None if value is None else Dimensions.model_validate_json(value)


@dataclass
class Draft:
    """No table, no declaration: the meta has nothing to say about it."""

    text: str


@dataclass
class Person(Entity):
    name: str


@dataclass
class Chapter(Entity):
    manuscript_id: str
    title: str


@dataclass
class Manuscript(Entity):
    title: str
    author_id: str
    editor_id: str | None = None
    reviewer_id: str | None = None
    notes: list[str] = field(default_factory=list)
    dimensions: Dimensions | None = None
    author: Person | None = None
    editor: Person | None = None
    reviewer: Person | None = None
    drafts: list[Draft] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)


class Manuscripts(EntityCollection[Manuscript]):
    pass


desk = registry()
person_table = entity_table("person", desk.metadata, Column("name", Text, nullable=False))
manuscript_table = entity_table(
    "manuscript",
    desk.metadata,
    Column("title", Text, nullable=False),
    Column("author_id", Text, ForeignKey("person.id"), nullable=False),
    Column("editor_id", Text, ForeignKey("person.id")),
    Column("reviewer_id", Text, ForeignKey("person.id")),
    Column("notes", JsonText, nullable=False),
    Column("dimensions", EmbeddedDimensions),
)
chapter_table = entity_table(
    "chapter",
    desk.metadata,
    Column("manuscript_id", Text, ForeignKey("manuscript.id"), nullable=False),
    Column("title", Text, nullable=False),
    Column("position", Integer, nullable=False, default=0),
)


def declare() -> None:
    """Called more than once on purpose: a second call must change nothing."""
    map_aggregates(
        desk,
        {Person: person_table, Manuscript: manuscript_table, Chapter: chapter_table},
        relations={
            Manuscript: {"editor": Relation.foreign_key(Person, identified_by="editor_id")}
        },
    )
