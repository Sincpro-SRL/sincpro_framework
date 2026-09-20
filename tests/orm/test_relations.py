"""Every kind of relation, asked for through the specification and answered once per node.

The page under test expands six relations of six kinds at once, and the statement counter is
the assertion that matters: one statement per named node, whatever the number of rows.
"""

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from typing import Any, cast

import pytest
from sqlalchemy import event

from sincpro_framework.ddd.criteria import (
    Condition,
    Criteria,
    Operator,
    Specification,
    parse_order,
)
from sincpro_framework.ddd.criteria.pagination import Pagination
from sincpro_framework.ddd.entity.entity_collection import Count, EntityCollection
from sincpro_framework.ddd.exceptions import RelationNotResolved
from sincpro_framework.ddd.query import ResponsePaginatedQuery
from sincpro_framework.orm.sqlalchemy.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .related_models import (
    CALLS,
    Author,
    Authors,
    Book,
    Books,
    Publisher,
    Review,
    declare_library,
    open_pair,
    populate,
    reviews_bus,
)

RELATIONAL = ("many2one", "one2many", "many2many")


class ResponseBooks(ResponsePaginatedQuery):
    books: list[Book]


class ResponseAuthors(ResponsePaginatedQuery):
    authors: list[Author]


@pytest.fixture(scope="module")
def library(tmp_path_factory) -> dict:
    """The library and the reviews context, mapped once for the module, populated once."""
    shelf, reviews = open_pair(tmp_path_factory.mktemp("relations"))
    declare_library(reviews_bus(reviews))
    records = populate(shelf, reviews)
    return {"shelf": shelf, "reviews": reviews, **records}


@pytest.fixture
def shelf(library) -> Repository:
    return library["shelf"]


@pytest.fixture
def statements(shelf) -> Callable[[], AbstractContextManager[list[str]]]:
    @contextmanager
    def counting() -> Generator[list[str]]:
        seen: list[str] = []

        def record(connection, cursor, statement, parameters, context, many) -> None:
            seen.append(statement)

        event.listen(shelf.database.engine, "before_cursor_execute", record)
        try:
            yield seen
        finally:
            event.remove(shelf.database.engine, "before_cursor_execute", record)

    return counting


def by_title(page) -> dict[str, Book]:
    return {book.title: book for book in page}


def page_of(related: Any) -> EntityCollection:
    """A to-many is typed as a list on the aggregate and arrives as a collection."""
    return cast(EntityCollection, related)


def test_a_to_many_by_foreign_key_comes_ordered_cut_and_counted_per_parent(shelf, statements):
    """Ada has three books; asked for two, longest first, she gets two, a count of three and a
    cursor to go on inside her own books."""
    asked = Criteria(
        order=parse_order("name"),
        specification=Specification(
            {"books": Criteria(order=parse_order("-pages"), pagination=Pagination(limit=2))}
        ),
    )

    with statements() as seen:
        page = shelf.search(Authors, asked)

    ada, grace = page.items
    assert [book.title for book in ada.books] == ["Notes", "Engine"]
    assert ada.books.count is not None and ada.books.count.value == 3
    assert ada.books.cursor is not None
    assert [book.title for book in grace.books] == ["Compiler", "Memo"]
    assert grace.books.cursor is None
    selects = [s for s in seen if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 2  # the page of authors, and one statement for the node
    assert sum("row_number" in s.lower() for s in seen) == 1


def test_the_cursor_of_a_relation_pages_inside_that_parent(shelf, library):
    ada = library["authors"][0]
    first = shelf.search(
        Authors,
        Criteria(
            where=Condition(field="id", value=ada.id),
            specification=Specification(
                {
                    "books": Criteria(
                        order=parse_order("-pages"), pagination=Pagination(limit=2)
                    )
                }
            ),
        ),
    ).ensure_one()

    rest = shelf.search(
        Books,
        Criteria(
            where=Condition(field="author_id", value=ada.id),
            order=parse_order("-pages"),
            pagination=Pagination(limit=2).model_copy(
                update={
                    "strategy": Pagination().strategy.model_copy(
                        update={"token": first.books.cursor}
                    )
                }
            ),
        ),
    )

    assert [book.title for book in rest] == ["Sketch"]


def test_a_to_one_by_foreign_key_is_the_record_or_none(shelf):
    page = shelf.search(Books, Criteria(specification=Specification({"author": Criteria()})))

    assert {book.title: book.author.name for book in page if book.author} == {
        "Engine": "Ada",
        "Sketch": "Ada",
        "Notes": "Ada",
        "Compiler": "Grace",
        "Memo": "Grace",
    }


def test_a_many_to_many_goes_through_the_table_in_between(shelf):
    page = shelf.search(
        Books,
        Criteria(specification=Specification({"tags": Criteria(order=parse_order("label"))})),
    )
    books = by_title(page)

    assert [tag.label for tag in books["Engine"].tags] == ["logic", "math"]
    assert [tag.label for tag in books["Compiler"].tags] == ["code"]
    assert len(books["Memo"].tags) == 0 and page_of(books["Memo"].tags).count is not None


def test_a_list_of_ids_in_a_column_resolves_and_filters(shelf):
    page = shelf.search(
        Books,
        Criteria(
            specification=Specification(
                {
                    "sources": Criteria(
                        where=Condition(field="pages", value=400, operator=Operator.GT)
                    )
                }
            )
        ),
    )
    books = by_title(page)

    assert [s.title for s in books["Compiler"].sources] == ["Notes"]  # Engine has 300 pages
    assert [s.title for s in books["Engine"].sources] == ["Notes"]
    assert len(books["Memo"].sources) == 0


def test_another_context_answers_through_its_bus_with_the_reflected_criteria(shelf):
    """Reviews live in another database behind another bus; the node's filter, order and
    limit travel in the command, and the answer is cut per book."""
    page = shelf.search(
        Books,
        Criteria(
            specification=Specification(
                {
                    "reviews": Criteria(
                        where=Condition(field="stars", value=3, operator=Operator.GTE),
                        order=parse_order("-stars"),
                        pagination=Pagination(limit=1),
                    )
                }
            )
        ),
    )
    books = by_title(page)

    assert [(r.stars, r.text) for r in books["Engine"].reviews] == [(5, "great")]
    # The other side cut one review per book, as the partition asked; with the page per key
    # full, the count is «at least one», never a guess at the total.
    assert page_of(books["Engine"].reviews).count == Count(value=1, exact=False)
    assert [(r.stars, r.text) for r in books["Compiler"].reviews] == [(3, "fine")]
    assert len(books["Memo"].reviews) == 0
    # Three reviews came back for a page of five asked: the other side was not cut, so Memo's
    # empty answer is exact. Had the page been filled, it would say `exact=False`.
    assert page_of(books["Memo"].reviews).count == Count(value=0, exact=True)
    assert isinstance(books["Engine"].reviews[0], Review)


def test_a_function_answers_a_to_one_and_sees_the_keys_and_the_criteria(shelf):
    CALLS.clear()
    page = shelf.search(
        Books, Criteria(specification=Specification({"publisher": Criteria()}))
    )
    books = by_title(page)

    assert books["Engine"].publisher == Publisher(publisher_id="pub_1", name="Penguin")
    assert (
        books["Compiler"].publisher is not None
        and books["Compiler"].publisher.name == "Siglo XXI"
    )
    assert books["Memo"].publisher is None
    keys, criteria = CALLS[-1]
    assert sorted(keys) == ["pub_1", "pub_2"]
    assert criteria.expression is not None and criteria.expression.field == "publisher_id"  # type: ignore[union-attr]


def test_nesting_goes_as_deep_as_asked_with_one_statement_per_node(shelf, statements):
    """authors → books → tags: three nodes, three statements beside the page and its count."""
    asked = Criteria(
        specification=Specification(
            {
                "books": Criteria(
                    specification=Specification({"tags": Criteria(), "author": Criteria()})
                )
            }
        )
    )

    with statements() as seen:
        page = shelf.search(Authors, asked)

    ada = next(a for a in page if a.name == "Ada")
    engine = next(b for b in ada.books if b.title == "Engine")
    assert {tag.label for tag in engine.tags} == {"math", "logic"}
    assert engine.author is not None and engine.author.name == "Ada"
    selects = [s for s in seen if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 4  # the page (its count is known from it), books, tags, authors


def test_naming_a_relation_bare_brings_the_whole_record_and_no_relation_of_its_own(shelf):
    page = shelf.search(Books, Criteria(specification=Specification({"author": Criteria()})))
    author = page[0].author

    assert author is not None and author.name and author.id
    with pytest.raises(RelationNotResolved):
        author.books


def test_what_cannot_be_expanded_is_dropped_and_said(shelf):
    asked = Criteria(
        specification=Specification(
            {
                "title": Criteria(specification=Specification({"x": Criteria()})),
                "nope": Criteria(),
            }
        )
    )

    page = shelf.search(Books, asked)

    assert {(d.field, d.reason) for d in page.dropped} == {
        ("nope", "unknown_field"),
        ("title", "not_expandable"),
    }


def test_without_a_specification_nothing_is_resolved_and_touching_it_refuses(shelf):
    book = shelf.search(Books).first()
    assert book is not None

    with pytest.raises(RelationNotResolved):
        book.author


def test_inside_a_unit_of_work_a_relation_resolves_whole_on_first_touch(shelf, library):
    ada = library["authors"][0]
    with shelf.context() as unit:
        author = unit.get(Author, ada.id)
        assert author is not None
        titles = {book.title for book in author.books}
        first = author.books[0]
        assert first.author is not None and first.author.name == "Ada"  # a second hop, lazily

    assert titles == {"Engine", "Sketch", "Notes"}


def test_a_record_built_in_memory_keeps_what_its_constructor_was_given():
    fresh = Author(name="New")

    assert fresh.books == []


def test_the_definition_publishes_the_key_and_the_expanded_target(shelf):
    meta = describe(Book)
    assert (
        meta.relations["author"].identified_by == "author_id"
        and not meta.relations["author"].many
    )
    assert meta.relations["tags"].many and meta.relations["reviews"].relation == "Review"

    page = shelf.search(
        Books,
        Criteria(
            specification=Specification(
                {
                    "title": Criteria(),
                    "author": Criteria(specification=Specification({"name": Criteria()})),
                }
            )
        ),
    )
    assert page.meta is not None
    expanded = page.meta.only(
        page.meta
        and Specification(
            {
                "title": Criteria(),
                "author": Criteria(specification=Specification({"name": Criteria()})),
            }
        )
    )
    assert list(expanded.fields) == ["id", "title", "author"]
    assert expanded.relations["author"].definition is not None
    assert list(expanded.relations["author"].definition.fields) == ["id", "name"]


def test_the_answer_on_the_wire_shows_the_mask_and_the_relations_by_name(shelf):
    asked = Criteria(
        where=Condition(field="title", value="Engine"),
        specification=Specification(
            {
                "title": Criteria(),
                "author": Criteria(specification=Specification({"name": Criteria()})),
                "tags": Criteria(
                    order=parse_order("label"),
                    specification=Specification({"label": Criteria()}),
                ),
                "publisher": Criteria(),
            }
        ),
    )

    on_the_wire = ResponseBooks.of(shelf.search(Books, asked), asked).model_dump(mode="json")

    [engine] = on_the_wire["books"]
    assert set(engine) == {"id", "title", "author", "tags", "publisher"}
    assert set(engine["author"]) == {"id", "name"} and engine["author"]["name"] == "Ada"
    assert [t["label"] for t in engine["tags"]["items"]] == ["logic", "math"]
    assert (
        engine["tags"]["count"] == {"value": 2, "exact": True}
        and engine["tags"]["cursor"] is None
    )
    assert engine["publisher"] == {"publisher_id": "pub_1", "name": "Penguin"}
    fields = on_the_wire["model_meta_data"]["fields"]
    assert [n for n, f in fields.items() if f["type"] in RELATIONAL] == [
        "author",
        "tags",
        "publisher",
    ]


def test_without_a_specification_the_wire_shows_scalars_and_keys_only(shelf):
    on_the_wire = ResponseBooks.of(shelf.search(Books), Criteria()).model_dump()

    assert set(on_the_wire["books"][0]) == {
        "id",
        "created_at",
        "updated_at",
        "version",
        "title",
        "pages",
        "author_id",
        "publisher_id",
        "source_ids",
    }
    assert not [
        f
        for f in on_the_wire["model_meta_data"]["fields"].values()
        if f["type"] in RELATIONAL
    ]


def test_inside_a_unit_of_work_a_page_left_on_the_record_gives_way_to_the_whole(
    shelf, library
):
    """A client cut Ada's books to one. Outside a unit of work that page stays, as asked;
    inside one, a Feature touching the same relation gets all three, because a cut page is
    not what a rule can be decided on."""
    ada = library["authors"][0]
    cut = Criteria(
        where=Condition(field="id", value=ada.id),
        specification=Specification({"books": Criteria(pagination=Pagination(limit=1))}),
    )

    detached = shelf.search(Authors, cut).ensure_one()
    assert len(detached.books) == 1 and page_of(detached.books).is_partial

    with shelf.context() as unit:
        attached = unit.search(Authors, cut).ensure_one()
        assert len(attached.books) == 3


def test_a_page_per_key_travels_to_the_bus_so_no_parent_starves_another(shelf):
    """Engine has three reviews, Compiler one. One review per book, best first: without the
    partition the other side would answer Engine's two best and Compiler nothing."""
    page = shelf.search(
        Books,
        Criteria(
            where=Condition(
                field="title", value=["Engine", "Compiler"], operator=Operator.IN
            ),
            specification=Specification(
                {
                    "reviews": Criteria(
                        order=parse_order("-stars"), pagination=Pagination(limit=1)
                    )
                }
            ),
        ),
    )
    books = by_title(page)

    assert [r.stars for r in books["Engine"].reviews] == [5]
    assert [r.stars for r in books["Compiler"].reviews] == [3]


def test_explain_counts_one_statement_per_relation_node(shelf):
    """What a page with relations will cost, before it runs: the page, its count, and one
    call per node however deep the tree."""
    asked = Criteria(
        specification=Specification(
            {
                "author": Criteria(specification=Specification({"books": Criteria()})),
                "tags": Criteria(),
            }
        )
    )

    explained = shelf.explain(Books, asked)

    assert explained.relations == ["author", "author.books", "tags"]
    assert explained.statements == 2 + 3
    assert "SELECT" in explained.sql


def test_a_relation_that_resolves_to_nothing_says_so_instead_of_raising_a_key_error():
    """The resolver ran and wrote nothing, which a correct declaration never does — a
    `Relation` pointing the wrong way, or named for a field the aggregate does not declare.
    Left alone this surfaced as `KeyError: '_sincpro_resolved'`, a framework-internal key the
    caller has no way to connect to the line they wrote."""
    from dataclasses import dataclass

    import sqlalchemy as sa
    from sqlalchemy.orm import registry

    from sincpro_framework.ddd.entity import Entity
    from sincpro_framework.ddd.exceptions import RelationNotResolved
    from sincpro_framework.orm.sqlalchemy.data_mapper import (
        Relation,
        entity_table,
        map_aggregates,
    )
    from sincpro_framework.orm.sqlalchemy.database import Database
    from sincpro_framework.orm.sqlalchemy.repository import Repository

    @dataclass
    class Head(Entity):
        pass  # declares no `tails` field

    @dataclass
    class Tail(Entity):
        head_id: str = ""

    metadata = sa.MetaData()
    head_table = entity_table("rel_head", metadata)
    tail_table = entity_table(
        "rel_tail", metadata, sa.Column("head_id", sa.String, sa.ForeignKey("rel_head.id"))
    )
    map_aggregates(
        registry(),
        {Head: head_table, Tail: tail_table},
        relations={Head: {"tails": Relation.foreign_key(Tail, "head_id")}},
    )

    database = Database("sqlite://")
    metadata.create_all(database.engine)
    repository = Repository(database)
    head = Head()
    repository.save(head)

    with repository.context() as unit:
        stored = unit.get(Head, head.id)
        with pytest.raises(RelationNotResolved, match="resolved to nothing"):
            stored.tails  # type: ignore[attr-defined]  # noqa: B018
