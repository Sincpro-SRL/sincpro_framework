"""A relation is declared by writing its type, and by nothing else.

`list[X]` reads as many, `X | None` as one, and an attribute is a relation when its type is
another mapped class. There is no metadata object and no registration step.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy import Column, Integer, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.orm.sqlalchemy.model_introspection import describe

mapper_registry = registry()


@dataclass
class Shelf:
    shelf_id: str
    label: str

    books: "list[Book]" = ()  # type: ignore[assignment]


@dataclass
class Book:
    book_id: str
    title: str
    pages: int
    shelf_id: str

    shelf: "Shelf | None" = None


@dataclass
class Unmapped:
    unmapped_id: str


@dataclass
class Dangling:
    dangling_id: str
    ghost: object = None


# Written onto the class rather than spelled in the source: the failure under test is an
# annotation nothing can resolve at runtime, and a literal one would also be a type error at
# rest, which is a different thing.
Dangling.__annotations__["ghost"] = "Phantom | None"


mapper_registry.map_imperatively(
    Shelf,
    Table(
        "shelf",
        mapper_registry.metadata,
        Column("shelf_id", Text, primary_key=True),
        Column("label", Text, nullable=False),
    ),
)

mapper_registry.map_imperatively(
    Book,
    Table(
        "book",
        mapper_registry.metadata,
        Column("book_id", Text, primary_key=True),
        Column("title", Text, nullable=False),
        Column("pages", Integer, nullable=False),
        Column("shelf_id", Text, nullable=False),
    ),
)

mapper_registry.map_imperatively(
    Dangling,
    Table(
        "dangling",
        mapper_registry.metadata,
        Column("dangling_id", Text, primary_key=True),
    ),
)


def test_a_singular_annotation_reads_as_one():
    relations = describe(Book).relations

    assert set(relations) == {"shelf"}
    assert relations["shelf"].relation == "Shelf"
    assert relations["shelf"].many is False


def test_a_list_annotation_reads_as_many():
    relations = describe(Shelf).relations

    assert relations["books"].relation == "Book"
    assert relations["books"].many is True


def test_an_attribute_is_a_relation_because_its_type_is_mapped():
    """No registration: `Unmapped` is a class like any other, so it is not a relation."""

    @dataclass
    class Loose:
        loose_id: str
        stray: "Unmapped | None" = None

    mapper_registry.map_imperatively(
        Loose,
        Table(
            "loose",
            mapper_registry.metadata,
            Column("loose_id", Text, primary_key=True),
        ),
    )

    assert describe(Loose).relations == {}


def test_a_relation_is_a_field_of_its_own_type_beside_its_key():
    """`shelf_id` is the column and is filterable; `shelf` is the relation, in the same map,
    with a relational type and no operator of its own."""
    meta = describe(Book)

    assert meta.fields["shelf_id"].ops
    assert meta.fields["shelf"].type.is_relational
    assert meta.fields["shelf"].ops == () and not meta.fields["shelf"].sortable


def test_a_class_that_is_not_mapped_cannot_be_described():
    with pytest.raises(ContractViolation, match="not a mapped aggregate"):
        describe(Unmapped)


def test_an_annotation_that_does_not_resolve_fails_where_it_is_described():
    """At boot, not on the first request that asks for it."""
    with pytest.raises(ContractViolation, match="cannot be resolved"):
        describe(Dangling)
