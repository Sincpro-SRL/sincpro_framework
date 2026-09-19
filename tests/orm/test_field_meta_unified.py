"""One `FieldMeta` for every kind of field, read off Python and the tables, and one criteria
that asks for all of them at once.

Written before the code, as the specification of the unified definition:

- every field, scalar or relational or embedded, lives in `Meta.fields` under one name, with
  `type` saying which it is; `Meta.relations` is only a view over the relational ones;
- `many2one` and `one2many` are inferred from the foreign keys in the tables, nothing declared;
- an embedded value object is a field with a `definition` describing its shape;
- one criteria can filter, order, page, mask scalars, cut inside an embedded value and expand
  every relation in one request, and the whole tree is validated before anything runs.
"""

from typing import cast

import pytest

from sincpro_framework.ddd.criteria import (
    All,
    Condition,
    Criteria,
    Operator,
    Specification,
    parse_order,
)
from sincpro_framework.ddd.criteria.pagination import Pagination
from sincpro_framework.ddd.entity.entity_collection import EntityCollection
from sincpro_framework.ddd.entity.model_meta import FieldType
from sincpro_framework.ddd.exceptions import InvalidCriteria
from sincpro_framework.ddd.query import ResponsePaginatedQuery
from sincpro_framework.orm.sqlalchemy.data_mapper import relations_of
from sincpro_framework.orm.sqlalchemy.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .every_kind_models import Author, Format, Work, Works, declare, open_pair, populate
from .related_models import reviews_bus


class ResponseWorks(ResponsePaginatedQuery):
    works: list[Work]


@pytest.fixture(scope="module")
def library(tmp_path_factory) -> dict:
    works, reviews = open_pair(tmp_path_factory.mktemp("every_kind"))
    declare(reviews_bus(reviews))
    return {"repository": works, "reviews": reviews, **populate(works, reviews)}


@pytest.fixture
def works(library) -> Repository:
    return library["repository"]


META = None


@pytest.fixture
def meta(library):
    return describe(Work)


# ── the definition ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name, expected",
    [
        ("title", FieldType.TEXT),
        ("pages", FieldType.INTEGER),
        ("price", FieldType.NUMBER),
        ("published", FieldType.BOOLEAN),
        ("released_on", FieldType.DATE),
        ("printed_at", FieldType.DATETIME),
        ("keywords", FieldType.TEXT_LIST),
        ("caption", FieldType.TRANSLATED),
        ("format", FieldType.TEXT),
    ],
)
def test_every_scalar_type_is_described(meta, name, expected):
    assert meta.fields[name].type is expected


def test_an_enum_field_publishes_its_choices(meta):
    assert meta.fields["format"].choices == [Format.BOOK, Format.PAPER]


def test_relational_fields_live_in_the_same_map_with_their_own_type(meta):
    author = meta.fields["author"]
    assert author.type is FieldType.MANY2ONE
    assert author.relation == "Author" and author.identified_by == "author_id"

    tags = meta.fields["tags"]
    assert tags.type is FieldType.MANY2MANY and tags.relation == "Tag"

    reviews = meta.fields["reviews"]
    assert reviews.type is FieldType.ONE2MANY
    assert reviews.relation == "Review" and reviews.identified_by == "book_id"

    publisher = meta.fields["publisher"]
    assert publisher.type is FieldType.MANY2ONE
    assert publisher.relation == "Publisher" and publisher.identified_by == "publisher_id"


def test_a_one2many_is_inferred_from_the_foreign_key_on_the_other_table(library):
    works = describe(Author).fields["works"]

    assert works.type is FieldType.ONE2MANY
    assert works.relation == "Work" and works.identified_by == "author_id"


def test_foreign_keys_need_no_declaration(library):
    """Nobody wrote `Relation.foreign_key` for `author` or `works`; the tables said it."""
    assert relations_of(Work)["author"].kind == "foreign_key"
    assert relations_of(Author)["works"].kind == "foreign_key"


def test_an_embedded_value_is_a_field_with_a_shape(meta):
    size = meta.fields["size"]
    assert size.type is FieldType.EMBEDDED and not size.many and size.nullable
    assert size.definition is not None
    assert {n: f.type for n, f in size.definition.fields.items()} == {
        "width": FieldType.INTEGER,
        "height": FieldType.INTEGER,
        "unit": FieldType.TEXT,
    }

    sizes = meta.fields["sizes"]
    assert sizes.type is FieldType.EMBEDDED and sizes.many
    assert sizes.definition is not None and list(sizes.definition.fields) == [
        "width",
        "height",
        "unit",
    ]


def test_every_field_says_its_kind_in_one_word(meta):
    assert meta.fields["title"].kind == "scalar"
    assert meta.fields["size"].kind == "embedded" and meta.fields["sizes"].kind == "embedded"
    assert {meta.fields[n].kind for n in ("author", "tags", "reviews", "publisher")} == {
        "relation"
    }


def test_relations_is_a_view_over_the_relational_fields(meta):
    assert set(meta.relations) == {"author", "tags", "reviews", "publisher"}
    assert meta.relations["author"] is meta.fields["author"]


def test_a_relational_field_takes_no_operator_and_no_order(meta):
    assert meta.fields["author"].ops == () and not meta.fields["author"].sortable
    _, dropped = meta.accept(Condition(field="author", value="x"))
    assert [(d.field, d.reason) for d in dropped] == [("author", "unsupported_operator")]
    with pytest.raises(InvalidCriteria):
        meta.orderable("tags")


def test_only_cuts_scalars_embedded_shapes_and_relations_by_the_same_mask(meta):
    asked = Specification(
        {
            "title": Criteria(),
            "size": Criteria(specification=Specification({"width": Criteria()})),
            "author": Criteria(),
        }
    )

    cut = meta.only(asked)

    assert list(cut.fields) == ["id", "title", "size", "author"]
    assert cut.fields["size"].definition is not None
    assert list(cut.fields["size"].definition.fields) == ["width"]
    assert set(cut.relations) == {"author"}


# ── one criteria over everything ──────────────────────────────────────────────

EVERYTHING = Criteria(
    where=All(
        all=[
            Condition(field="published", value=True),
            Condition(field="pages", value=100, operator=Operator.GT),
            Condition(field="format", value=["book"], operator=Operator.IN),
            Condition(field="keywords", value="engine", operator=Operator.CONTAINS),
            Condition(
                field="printed_at",
                value=["2024-01-01T00:00:00", "2025-12-31T00:00:00"],
                operator=Operator.BETWEEN,
            ),
            Condition(field="released_on", value="2024-01-01", operator=Operator.GTE),
            Condition(field="price", value=10, operator=Operator.GT),
            Condition(field="caption", value="the", operator=Operator.LIKE),
        ]
    ),
    order=parse_order("-price"),
    pagination=Pagination(limit=2),
    specification=Specification(
        {
            "title": Criteria(),
            "price": Criteria(),
            "size": Criteria(specification=Specification({"width": Criteria()})),
            "sizes": Criteria(),
            "author": Criteria(specification=Specification({"name": Criteria()})),
            "tags": Criteria(order=parse_order("label")),
            "reviews": Criteria(
                where=Condition(field="stars", value=3, operator=Operator.GTE),
                order=parse_order("-stars"),
                pagination=Pagination(limit=1),
            ),
            "publisher": Criteria(),
        }
    ),
)


def test_one_criteria_filters_orders_pages_masks_and_expands_everything(works):
    page = works.search(Works, EVERYTHING)

    assert page.dropped == ()
    assert [w.title for w in page] == [
        "Compiler",
        "Engine",
    ]  # published, >100 pages, "engine", by price
    compiler, engine = page.items
    assert engine.author is not None and engine.author.name == "Ada"
    assert [t.label for t in engine.tags] == ["code", "math"]
    assert [(r.stars, r.text) for r in engine.reviews] == [(5, "great")]
    assert cast(EntityCollection, engine.reviews).count is not None
    assert engine.publisher is not None and engine.publisher.name == "Penguin"
    assert compiler.publisher is not None and compiler.publisher.name == "Siglo XXI"
    assert engine.size is not None and engine.size.width == 15
    assert len(engine.sizes) == 2


def test_the_wire_shows_every_kind_under_its_own_name_cut_by_the_mask(works):
    on_the_wire = ResponseWorks.of(works.search(Works, EVERYTHING), EVERYTHING).model_dump(
        mode="json"
    )

    [compiler, engine] = on_the_wire["works"]
    assert set(engine) == {
        "id",
        "title",
        "price",
        "size",
        "sizes",
        "author",
        "tags",
        "reviews",
        "publisher",
    }
    assert engine["price"] == "25.50" or engine["price"] == 25.5
    assert engine["size"] == {"width": 15}  # cut inside the embedded value
    assert engine["sizes"] == [
        {"width": 15, "height": 21, "unit": "cm"},
        {"width": 10, "height": 14, "unit": "in"},
    ]
    assert engine["author"] == {"id": engine["author"]["id"], "name": "Ada"}
    assert [t["label"] for t in engine["tags"]["items"]] == ["code", "math"]
    # Only one review with three stars or more; the other side filled the page it was asked
    # for, so the bus cannot promise the count is exact.
    assert engine["reviews"]["count"] == {"value": 1, "exact": False}
    assert engine["reviews"]["items"][0]["stars"] == 5
    assert engine["publisher"]["name"] == "Penguin"
    assert compiler["size"] == {"width": 20}

    definition = on_the_wire["model_meta_data"]
    assert list(definition["fields"]) == [
        "id",
        "title",
        "price",
        "size",
        "sizes",
        "author",
        "tags",
        "reviews",
        "publisher",
    ]
    assert definition["fields"]["author"]["type"] == "many2one"
    assert definition["fields"]["author"]["kind"] == "relation"
    assert definition["fields"]["size"]["kind"] == "embedded"
    assert definition["fields"]["title"]["kind"] == "scalar"
    assert list(definition["fields"]["author"]["definition"]["fields"]) == ["id", "name"]
    assert list(definition["fields"]["size"]["definition"]["fields"]) == ["width"]


def test_the_whole_tree_is_validated_before_anything_runs(works):
    asked = Criteria(
        specification=Specification(
            {
                "title": Criteria(specification=Specification({"x": Criteria()})),
                "size": Criteria(specification=Specification({"depth": Criteria()})),
                "author": Criteria(specification=Specification({"nope": Criteria()})),
                "ghost": Criteria(),
            }
        )
    )

    page = works.search(Works, asked)

    assert {(d.field, d.reason) for d in page.dropped} == {
        ("title", "not_expandable"),
        ("depth", "unknown_field"),
        ("nope", "unknown_field"),
        ("ghost", "unknown_field"),
    }


def test_without_a_specification_keys_travel_and_relations_do_not(works):
    on_the_wire = ResponseWorks.of(works.search(Works), Criteria()).model_dump()

    record = on_the_wire["works"][0]
    assert {"author_id", "publisher_id", "size", "sizes"} <= set(record)
    assert not ({"author", "tags", "reviews", "publisher"} & set(record))
    assert (
        on_the_wire["model_meta_data"]["relations"] == {}
        if "relations" in on_the_wire["model_meta_data"]
        else True
    )
