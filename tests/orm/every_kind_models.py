"""One aggregate with every kind of field the definition can describe, for the tests of the
unified `FieldMeta`.

    scalars       text · integer · number · boolean · date · datetime · text[] · translated · choices
    embedded      a value object in a JSON column, one and a list of them
    many2one      Work.author, inferred from the foreign key on `work.author_id`
    one2many      Author.works, inferred from that same foreign key
    many2many     Work.tags through `work_tag`, declared: no foreign key says it
    elsewhere     Work.reviews from another context through its bus; Work.publisher from a resolver

Only what Python and the tables cannot say is declared. Everything else is read off them.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    Table,
    Text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import registry
from sqlalchemy.types import TypeDecorator

from sincpro_framework.ddd.entity import Entity, Translated
from sincpro_framework.ddd.entity_collection import EntityCollection
from sincpro_framework.orm.sqlalchemy.custom_fields import JsonText, TranslatedText
from sincpro_framework.orm.sqlalchemy.data_mapper import (
    Relation,
    entity_table,
    map_aggregates,
)
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .related_models import (
    CommandSearchReviews,
    Publisher,
    Review,
    fetch_publishers,
    reviews_registry,
)


class Format(StrEnum):
    BOOK = "book"
    PAPER = "paper"


@dataclass
class Shape:
    """A value object: no identity of its own, it travels inside the row."""

    width: int
    height: int
    unit: str = "cm"


class EmbeddedShape(TypeDecorator):
    """How this project stores a `Shape` in a column: as JSON. The framework does not know
    this type; it reads the annotation."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else json.dumps(asdict(value))

    def process_result_value(self, value: str | None, dialect: Dialect) -> Any:
        return None if value is None else Shape(**json.loads(value))


class EmbeddedShapes(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else json.dumps([asdict(one) for one in value])

    def process_result_value(self, value: str | None, dialect: Dialect) -> Any:
        return None if value is None else [Shape(**one) for one in json.loads(value)]


@dataclass
class Author(Entity):
    name: str
    works: list["Work"] = field(default_factory=list)


@dataclass
class Tag(Entity):
    label: str


@dataclass
class Work(Entity):
    title: str
    pages: int
    price: Decimal
    published: bool
    released_on: date
    printed_at: datetime
    keywords: list[str]
    caption: dict[str, str]
    format: Format
    author_id: str
    publisher_id: str | None = None
    size: Shape | None = None
    sizes: list[Shape] = field(default_factory=list)
    author: Author | None = None
    tags: list[Tag] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    publisher: Publisher | None = None

    @classmethod
    def translations(cls) -> Translated:
        return {"name": {"default": "Work"}, "labels": {"title": {"default": "Title"}}}


class Works(EntityCollection[Work]):
    pass


class Authors(EntityCollection[Author]):
    pass


shelf = registry()
author_table = entity_table("author", shelf.metadata, Column("name", Text, nullable=False))
tag_table = entity_table("tag", shelf.metadata, Column("label", Text, nullable=False))
work_table = entity_table(
    "work",
    shelf.metadata,
    Column("title", Text, nullable=False),
    Column("pages", Integer, nullable=False),
    Column("price", Numeric(10, 2), nullable=False),
    Column("published", Boolean, nullable=False),
    Column("released_on", Date, nullable=False),
    Column("printed_at", DateTime, nullable=False),
    Column("keywords", JsonText, nullable=False),
    Column("caption", TranslatedText, nullable=False),
    Column("format", Enum(Format, native_enum=False), nullable=False),
    Column("author_id", Text, ForeignKey("author.id"), nullable=False),
    Column("publisher_id", Text),
    Column("size", EmbeddedShape),
    Column("sizes", EmbeddedShapes, nullable=False),
)
work_tag = Table(
    "work_tag",
    shelf.metadata,
    Column("work_id", Text, ForeignKey("work.id"), primary_key=True),
    Column("tag_id", Text, ForeignKey("tag.id"), primary_key=True),
)


def declare(reviews_bus) -> None:
    """Only what cannot be read: the table in between, and what lives elsewhere."""
    map_aggregates(
        shelf,
        {Author: author_table, Tag: tag_table, Work: work_table},
        relations={
            Work: {
                "tags": Relation.many_to_many(
                    Tag, through=work_tag, this_key="work_id", related_key="tag_id"
                ),
                "reviews": Relation.bus(
                    Review, reviews_bus, CommandSearchReviews, identified_by="book_id"
                ),
                "publisher": Relation.resolved_by(
                    Publisher, identified_by="publisher_id", resolver=fetch_publishers
                ),
            }
        },
    )


def populate(works: Repository, reviews: Repository) -> dict[str, list]:
    ada, grace = Author(name="Ada"), Author(name="Grace")
    math, code = Tag(label="math"), Tag(label="code")
    engine = Work(
        title="Engine",
        pages=300,
        price=Decimal("25.50"),
        published=True,
        released_on=date(2024, 3, 1),
        printed_at=datetime(2024, 3, 1, 9, 0),
        keywords=["analytical", "engine"],
        caption={"default": "The engine", "es": "La máquina"},
        format=Format.BOOK,
        author_id=ada.id,
        publisher_id="pub_1",
        size=Shape(width=15, height=21),
        sizes=[Shape(width=15, height=21), Shape(width=10, height=14, unit="in")],
    )
    sketch = Work(
        title="Sketch",
        pages=40,
        price=Decimal("5.00"),
        published=False,
        released_on=date(2024, 6, 1),
        printed_at=datetime(2024, 6, 1, 9, 0),
        keywords=["notes"],
        caption={"default": "A sketch"},
        format=Format.PAPER,
        author_id=ada.id,
    )
    compiler = Work(
        title="Compiler",
        pages=200,
        price=Decimal("40.00"),
        published=True,
        released_on=date(2025, 1, 15),
        printed_at=datetime(2025, 1, 15, 9, 0),
        keywords=["compiler", "engine"],
        caption={"default": "The compiler"},
        format=Format.BOOK,
        author_id=grace.id,
        publisher_id="pub_2",
        size=Shape(width=20, height=28),
    )
    with works.context() as unit:
        unit.session.add_all([ada, grace, math, code, engine, sketch, compiler])
        unit.flush()
        unit.session.execute(
            work_tag.insert(),
            [
                {"work_id": engine.id, "tag_id": math.id},
                {"work_id": engine.id, "tag_id": code.id},
                {"work_id": compiler.id, "tag_id": code.id},
            ],
        )
    with reviews.context() as unit:
        unit.session.add_all(
            [
                Review(book_id=engine.id, stars=5, text="great"),
                Review(book_id=engine.id, stars=2, text="meh"),
                Review(book_id=compiler.id, stars=4, text="good"),
            ]
        )
    return {
        "authors": [ada, grace],
        "works": [engine, sketch, compiler],
        "tags": [math, code],
    }


def open_pair(tmp_path) -> tuple[Repository, Repository]:
    works_db = Database(f"sqlite:///{tmp_path}/works.sqlite3")
    reviews_db = Database(f"sqlite:///{tmp_path}/reviews.sqlite3")
    shelf.metadata.create_all(works_db.engine)
    reviews_registry.metadata.create_all(reviews_db.engine)
    return Repository(works_db), Repository(reviews_db)
