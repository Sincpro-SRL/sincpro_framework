"""What wins when a name could be read two ways, and what happens at the edges: empty values,
a value object deserialised from JSON, a pointer nobody can resolve, a page with nothing on it.
"""

from typing import cast

import pytest

from sincpro_framework.ddd.criteria import Condition, Criteria, Specification, parse_order
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.entity_collection import EntityCollection
from sincpro_framework.ddd.entity.model_meta import FieldType
from sincpro_framework.ddd.query import ResponsePaginatedQuery
from sincpro_framework.orm.sqlalchemy.data_mapper import relations_of
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .precedence_models import (
    Chapter,
    Dimensions,
    Manuscript,
    Manuscripts,
    Person,
    declare,
    desk,
)


class ResponseManuscripts(ResponsePaginatedQuery):
    manuscripts: list[Manuscript]


@pytest.fixture(scope="module")
def desk_repository(tmp_path_factory) -> Repository:
    declare()
    declare()  # idempotent on purpose
    database = Database(f"sqlite:///{tmp_path_factory.mktemp('precedence')}/desk.sqlite3")
    desk.metadata.create_all(database.engine)
    repository = Repository(database)
    ada, bob = Person(name="Ada"), Person(name="Bob")
    full = Manuscript(
        title="Full",
        author_id=ada.id,
        editor_id=bob.id,
        reviewer_id=bob.id,
        notes=["first", "second"],
        dimensions=Dimensions(width=15, height=21),
    )
    bare = Manuscript(title="Bare", author_id=bob.id)
    with repository.context() as unit:
        unit.session.add_all([ada, bob, full, bare])
        unit.flush()
        unit.session.add_all(
            [
                Chapter(manuscript_id=full.id, title="One"),
                Chapter(manuscript_id=full.id, title="Two"),
            ]
        )
    return repository


@pytest.fixture
def meta(desk_repository):
    return describe(Manuscript)


def by_title(page) -> dict[str, Manuscript]:
    return {m.title: m for m in page}


# ── precedence ────────────────────────────────────────────────────────────────


def test_a_column_with_a_list_annotation_is_a_field_never_a_relation(meta):
    assert meta.fields["notes"].type is FieldType.TEXT_LIST
    assert "notes" not in meta.relations
    assert "notes" not in relations_of(Manuscript)


def test_with_two_foreign_keys_the_column_named_after_the_attribute_is_inferred(meta):
    author = meta.fields["author"]
    assert author.type is FieldType.MANY2ONE and author.identified_by == "author_id"
    assert relations_of(Manuscript)["author"].kind == "foreign_key"


def test_a_declaration_wins_over_inference(meta):
    editor = meta.fields["editor"]
    assert editor.type is FieldType.MANY2ONE and editor.identified_by == "editor_id"
    assert editor.nullable  # from the column it hangs on


def test_a_pointer_nothing_identifies_is_published_but_not_expandable(meta, desk_repository):
    """`reviewer` has three candidate keys and none is `reviewer_id`... except there is one:
    the rule takes `<name>_id` when it exists."""
    reviewer = meta.fields["reviewer"]
    assert reviewer.type is FieldType.MANY2ONE and reviewer.identified_by == "reviewer_id"


def test_a_one2many_is_inferred_from_the_child_table(meta):
    chapters = meta.fields["chapters"]
    assert chapters.type is FieldType.ONE2MANY
    assert chapters.relation == "Chapter" and chapters.identified_by == "manuscript_id"


def test_a_dataclass_with_no_table_and_no_declaration_is_ignored(meta):
    assert "drafts" not in meta.fields
    assert "drafts" not in relations_of(Manuscript)


def test_mapping_twice_neither_duplicates_nor_replaces(meta):
    declare()
    assert set(relations_of(Manuscript)) == {"author", "editor", "reviewer", "chapters"}
    assert describe(Manuscript).fields["editor"].identified_by == "editor_id"


# ── a value object deserialised from JSON ─────────────────────────────────────


def test_a_pydantic_value_object_in_a_json_column_is_embedded_with_its_shape(meta):
    dimensions = meta.fields["dimensions"]
    assert (
        dimensions.type is FieldType.EMBEDDED and dimensions.nullable and not dimensions.many
    )
    assert dimensions.definition is not None
    assert {n: f.type for n, f in dimensions.definition.fields.items()} == {
        "width": FieldType.INTEGER,
        "height": FieldType.INTEGER,
        "unit": FieldType.TEXT,
    }


def test_the_json_comes_back_as_the_model_and_is_cut_by_the_mask(desk_repository):
    asked = Criteria(
        specification=Specification(
            {
                "title": Criteria(),
                "dimensions": Criteria(specification=Specification({"height": Criteria()})),
            }
        )
    )
    page = desk_repository.search(Manuscripts, asked)
    manuscripts = by_title(page)

    assert isinstance(manuscripts["Full"].dimensions, Dimensions)
    assert manuscripts["Full"].dimensions.height == 21
    assert manuscripts["Bare"].dimensions is None

    on_the_wire = ResponseManuscripts.of(page, asked).model_dump(mode="json")
    written = {m["title"]: m for m in on_the_wire["manuscripts"]}
    assert written["Full"]["dimensions"] == {
        "height": 21
    }  # a value object keeps only what was named
    assert written["Bare"]["dimensions"] is None


# ── the edges ─────────────────────────────────────────────────────────────────


def test_empty_values_come_back_empty_not_missing(desk_repository):
    asked = Criteria(
        specification=Specification(
            {
                "title": Criteria(),
                "notes": Criteria(),
                "editor": Criteria(),
                "chapters": Criteria(order=parse_order("title")),
            }
        )
    )
    page = desk_repository.search(Manuscripts, asked)
    bare = by_title(page)["Bare"]

    assert bare.notes == []
    assert bare.editor is None
    chapters = cast(EntityCollection, bare.chapters)
    assert len(chapters) == 0 and chapters.count is not None and chapters.count.value == 0

    written = {
        m["title"]: m for m in ResponseManuscripts.of(page, asked).model_dump()["manuscripts"]
    }
    assert written["Bare"]["notes"] == [] and written["Bare"]["editor"] is None
    assert written["Bare"]["chapters"] == {
        "items": [],
        "count": {"value": 0, "exact": True},
        "cursor": None,
    }
    assert [c["title"] for c in written["Full"]["chapters"]["items"]] == ["One", "Two"]


def test_a_page_with_no_records_and_a_full_specification_is_just_empty(desk_repository):
    asked = Criteria(
        where=Condition(field="title", value="Nobody"),
        specification=Specification(
            {"author": Criteria(), "chapters": Criteria(), "dimensions": Criteria()}
        ),
    )

    page = desk_repository.search(Manuscripts, asked)
    on_the_wire = ResponseManuscripts.of(page, asked).model_dump()

    assert len(page) == 0 and page.dropped == ()
    assert on_the_wire["manuscripts"] == []
    assert on_the_wire["model_meta_data"]["fields"]["author"]["type"] == "many2one"


def test_an_empty_specification_node_on_an_embedded_value_brings_it_whole(desk_repository):
    asked = Criteria(specification=Specification({"dimensions": Criteria()}))

    written = ResponseManuscripts.of(
        desk_repository.search(Manuscripts, asked), asked
    ).model_dump()
    full = next(m for m in written["manuscripts"] if m.get("dimensions"))

    assert full["dimensions"] == {"width": 15, "height": 21, "unit": "cm"}


def test_describing_before_mapping_does_not_freeze_a_definition_without_relations():
    """`describe` is cached per class; installing relations afterwards must be seen."""
    from dataclasses import dataclass, field

    from sqlalchemy import Column, ForeignKey, Text
    from sqlalchemy.orm import registry

    from sincpro_framework.orm.sqlalchemy.data_mapper import entity_table, map_aggregates

    @dataclass
    class Pet(Entity):
        owner_id: str
        name: str

    @dataclass
    class Owner(Entity):
        name: str
        pets: list[Pet] = field(default_factory=list)

    fresh = registry()
    owner_table = entity_table("owner_late", fresh.metadata, Column("name", Text))
    pet_table = entity_table(
        "pet_late",
        fresh.metadata,
        Column("owner_id", Text, ForeignKey("owner_late.id")),
        Column("name", Text),
    )
    map_aggregates(fresh, {Owner: owner_table})
    assert "pets" not in describe(Owner).fields  # described before Pet was mapped

    map_aggregates(fresh, {Pet: pet_table})

    assert describe(Owner).fields["pets"].type is FieldType.ONE2MANY
