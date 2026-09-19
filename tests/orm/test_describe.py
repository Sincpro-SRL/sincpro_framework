"""That the model definition is read off the model, and nothing is declared.

The whole claim of this design is that adding a column makes it filterable and removing one
stops it being offered, so what is asserted is what the mapping says and not a list somebody
maintains.

Two things here would be silent failures if they regressed. `tags` must describe as a list,
because the annotation says `list[str]` while the column says `Text`, and a client told "text"
would offer `like` on a JSON array. And `owner` must come back not sortable, because it is
nullable and a keyset cursor drops rows holding NULL in a compared column — the one failure in
this design that loses data without raising anything.
"""

from sincpro_framework.ddd.criteria import Operator
from sincpro_framework.ddd.entity.model_meta import FieldType
from sincpro_framework.orm.sqlalchemy.model_introspection import describe

from .models import Thing


def test_the_aggregate_names_itself_and_its_identity():
    meta = describe(Thing)

    assert meta.aggregate == "Thing"
    assert meta.identity == "thing_id"
    assert meta.default_order == "-thing_id"


def test_every_mapped_column_is_described_and_nothing_else_is():
    meta = describe(Thing)

    assert set(meta.fields) == {"thing_id", "name", "size", "tags", "made_at", "owner"}


def test_the_logical_type_comes_from_the_annotation_not_the_column():
    """`tags` is Text on disk and a list in the model; the client needs the second."""
    fields = describe(Thing).fields

    assert fields["tags"].type is FieldType.TEXT_LIST
    assert fields["size"].type is FieldType.INTEGER
    assert fields["made_at"].type is FieldType.DATETIME
    assert fields["name"].type is FieldType.TEXT


def test_the_operators_offered_match_the_type():
    fields = describe(Thing).fields

    assert Operator.LIKE in fields["name"].ops
    assert Operator.LIKE not in fields["size"].ops
    assert Operator.GT in fields["size"].ops
    # `eq`/`ne` over a list column asks about the list itself: «tagged with nothing». And
    # `is null` because the fixture's column is nullable, the way a column added later is.
    assert fields["tags"].ops == (
        Operator.CONTAINS,
        Operator.NOT_CONTAINS,
        Operator.EQ,
        Operator.NE,
        Operator.IS_NULL,
    )


def test_a_nullable_column_gains_is_null_and_loses_sortability():
    fields = describe(Thing).fields

    assert fields["owner"].nullable
    assert Operator.IS_NULL in fields["owner"].ops
    assert not fields["owner"].sortable


def test_a_not_null_column_is_sortable_and_has_no_is_null():
    fields = describe(Thing).fields

    assert not fields["made_at"].nullable
    assert fields["made_at"].sortable
    assert Operator.IS_NULL not in fields["made_at"].ops


def test_a_list_column_is_never_sortable():
    """There is no ordering over a JSON array that a keyset could page through."""
    assert not describe(Thing).fields["tags"].sortable


def test_the_identity_is_the_first_field_the_class_declares():
    """Context: pins the convention `EntityCollection.identity_of` relies on. The mapper knows the
    primary key and the collection knows only the class, so the two agree by convention — and
    a convention nothing checks is one that breaks on the aggregate nobody looked at."""
    from dataclasses import fields as dataclass_fields

    assert describe(Thing).identity == dataclass_fields(Thing)[0].name
