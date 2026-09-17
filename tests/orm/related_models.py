"""A small library, for the relation tests: the six ways a relation is declared, on purpose
all at once, so one page can expand every kind.

    Author.books      to-many by foreign key, the key on Book
    Book.author       to-one by foreign key, the key on Book
    Book.tags         many-to-many through `book_tag`
    Book.sources      to-many by a list of ids held in a JSON column
    Book.reviews      to-many from another bounded context, through a command on its bus
    Book.publisher    to-one from a resolver that could be HTTP, raw SQL or anything

Reviews live in their own database with their own registry and bus, so the bus resolver is a
real second context and not the same tables under another name.
"""

from dataclasses import dataclass, field

from sqlalchemy import Column, ForeignKey, Integer, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework import Feature, UseFramework
from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity_collection import EntityCollection
from sincpro_framework.ddd.query import Query, ResponsePaginatedQuery
from sincpro_framework.orm.sqlalchemy.custom_fields import JsonText
from sincpro_framework.orm.sqlalchemy.data_mapper import (
    Relation,
    entity_table,
    map_aggregates,
)
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository


@dataclass
class Publisher:
    """Not mapped anywhere: what a function resolver answers."""

    publisher_id: str
    name: str


@dataclass
class Author(Entity):
    name: str
    books: list["Book"] = field(default_factory=list)


@dataclass
class Tag(Entity):
    label: str


@dataclass
class Book(Entity):
    title: str
    pages: int
    author_id: str
    publisher_id: str | None = None
    source_ids: list[str] = field(default_factory=list)
    author: Author | None = None
    tags: list[Tag] = field(default_factory=list)
    sources: list["Book"] = field(default_factory=list)
    reviews: list["Review"] = field(default_factory=list)
    publisher: Publisher | None = None


@dataclass
class Review(Entity):
    """The other context's aggregate."""

    book_id: str
    stars: int
    text: str


class Books(EntityCollection[Book]):
    pass


class Authors(EntityCollection[Author]):
    pass


class Reviews(EntityCollection[Review]):
    pass


# ----------------------------------------------------------------- the library's own database
library = registry()
author_table = entity_table("author", library.metadata, Column("name", Text, nullable=False))
tag_table = entity_table("tag", library.metadata, Column("label", Text, nullable=False))
book_table = entity_table(
    "book",
    library.metadata,
    Column("title", Text, nullable=False),
    Column("pages", Integer, nullable=False),
    Column("author_id", Text, ForeignKey("author.id"), nullable=False),
    Column("publisher_id", Text),
    Column("source_ids", JsonText, nullable=False),
)
book_tag = Table(
    "book_tag",
    library.metadata,
    Column("book_id", Text, ForeignKey("book.id"), primary_key=True),
    Column("tag_id", Text, ForeignKey("tag.id"), primary_key=True),
)

# ----------------------------------------------------------------- the reviews context
reviews_registry = registry()
review_table = entity_table(
    "review",
    reviews_registry.metadata,
    Column("book_id", Text, nullable=False),
    Column("stars", Integer, nullable=False),
    Column("text", Text, nullable=False),
)
map_aggregates(reviews_registry, {Review: review_table})


class CommandSearchReviews(Query):
    pass


class ResponseReviews(ResponsePaginatedQuery):
    reviews: list[Review]


def reviews_bus(repository: Repository) -> UseFramework:
    bus = UseFramework("reviews", log_after_execution=False)
    bus.add_dependency("repository", repository)

    @bus.feature(CommandSearchReviews)
    class SearchReviews(Feature):
        repository: Repository

        def execute(self, dto: CommandSearchReviews) -> ResponseReviews:
            return ResponseReviews.of(
                self.repository.search(Reviews, dto.criteria), dto.criteria
            )

    return bus


PUBLISHERS = {
    "pub_1": Publisher(publisher_id="pub_1", name="Penguin"),
    "pub_2": Publisher(publisher_id="pub_2", name="Siglo XXI"),
}

CALLS: list[tuple[list, Criteria]] = []
"""What the function resolver was asked, so a test can see the reflected criteria."""


def fetch_publishers(keys, criteria: Criteria) -> list[Publisher]:
    CALLS.append((list(keys), criteria))
    return [PUBLISHERS[key] for key in keys if key in PUBLISHERS]


def declare_library(reviews: UseFramework) -> None:
    """Maps the library once, with every relation, against the given reviews context."""
    map_aggregates(
        library,
        {Author: author_table, Tag: tag_table, Book: book_table},
        relations={
            Book: {
                "tags": Relation.many_to_many(
                    Tag, through=book_tag, this_key="book_id", related_key="tag_id"
                ),
                "sources": Relation.id_list(Book, identified_by="source_ids"),
                "reviews": Relation.bus(
                    Review, reviews, CommandSearchReviews, identified_by="book_id"
                ),
                "publisher": Relation.resolved_by(
                    Publisher, identified_by="publisher_id", resolver=fetch_publishers
                ),
            },
        },
    )


def populate(shelf: Repository, reviews: Repository) -> dict[str, list]:
    """Two authors, five books, three tags, reviews on some books.

    Ada     writes b1 (300 pages, tags math+logic, from b3), b2 (120, tag math), b3 (500)
    Grace   writes b4 (200, publisher pub_2), b5 (80, no publisher, no reviews)
    """
    ada, grace = Author(name="Ada"), Author(name="Grace")
    math, logic, code = Tag(label="math"), Tag(label="logic"), Tag(label="code")
    b3 = Book(title="Notes", pages=500, author_id=ada.id, publisher_id="pub_1")
    b1 = Book(
        title="Engine", pages=300, author_id=ada.id, publisher_id="pub_1", source_ids=[b3.id]
    )
    b2 = Book(title="Sketch", pages=120, author_id=ada.id, publisher_id="pub_1")
    b4 = Book(
        title="Compiler",
        pages=200,
        author_id=grace.id,
        publisher_id="pub_2",
        source_ids=[b1.id, b3.id],
    )
    b5 = Book(title="Memo", pages=80, author_id=grace.id, publisher_id=None)
    books = [b1, b2, b3, b4, b5]
    with shelf.context() as unit:
        unit.session.add_all([ada, grace, math, logic, code, *books])
        unit.flush()
        unit.session.execute(
            book_tag.insert(),
            [
                {"book_id": b1.id, "tag_id": math.id},
                {"book_id": b1.id, "tag_id": logic.id},
                {"book_id": b2.id, "tag_id": math.id},
                {"book_id": b4.id, "tag_id": code.id},
            ],
        )
    with reviews.context() as unit:
        unit.session.add_all(
            [
                Review(book_id=b1.id, stars=5, text="great"),
                Review(book_id=b1.id, stars=2, text="meh"),
                Review(book_id=b1.id, stars=4, text="good"),
                Review(book_id=b4.id, stars=3, text="fine"),
            ]
        )
    return {"authors": [ada, grace], "books": books, "tags": [math, logic, code]}


def open_pair(tmp_path) -> tuple[Repository, Repository]:
    shelf_db = Database(f"sqlite:///{tmp_path}/library.sqlite3")
    reviews_db = Database(f"sqlite:///{tmp_path}/reviews.sqlite3")
    library.metadata.create_all(shelf_db.engine)
    reviews_registry.metadata.create_all(reviews_db.engine)
    return Repository(shelf_db), Repository(reviews_db)
