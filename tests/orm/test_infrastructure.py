"""The plumbing: the session, the column types, and the refusals of the engine.

These are the paths that only run when something goes wrong — a rollback, a call the engine
will not answer — so nothing else in the suite reaches them. A path nobody has ever run is a
path nobody knows works.
"""

from datetime import datetime

import pytest
from sqlalchemy.dialects.sqlite import dialect as SQLiteDialect

from sincpro_framework.ddd.criteria import Condition, CountMode, Criteria, Operator
from sincpro_framework.ddd.criteria.pagination import Pagination
from sincpro_framework.ddd.entity.entity_collection import Count, EntityCollection
from sincpro_framework.ddd.entity.model_meta import FieldType
from sincpro_framework.ddd.exceptions import ContractViolation, InvalidCriteria
from sincpro_framework.orm.sqlalchemy.custom_fields import JsonText, TranslatedText
from sincpro_framework.orm.sqlalchemy.sql_translator import where_clause

from .models import Thing, Things


def test_a_session_rolls_back_what_a_failure_left_behind(store):
    with pytest.raises(RuntimeError):
        with store.database.session() as session:
            session.add(
                Thing(
                    thing_id="th_rollback",
                    name="never",
                    size=0,
                    tags=[],
                    made_at=datetime(2026, 1, 1),
                )
            )
            raise RuntimeError("something went wrong halfway")

    assert store.browse(Things, ["th_rollback"]).ids == []


def test_a_collection_that_does_not_say_what_it_holds_is_refused(store):
    class Nameless(EntityCollection):
        pass

    with pytest.raises(ContractViolation, match="does not say which aggregate"):
        store.search(Nameless, Criteria(pagination=Pagination(limit=1)))


def test_grouping_by_nothing_is_refused_rather_than_answered(store):
    with pytest.raises(InvalidCriteria, match="at least one field"):
        store.group_by(Things, [])


def test_folding_nothing_is_refused_and_says_how_it_should_read(store):
    with pytest.raises(InvalidCriteria, match="at least one measure"):
        store.measures(Things)

    with pytest.raises(InvalidCriteria, match="is written"):
        store.measures(Things, None, total="size")


def test_counting_and_grouping_both_honour_the_filter(store):
    narrow = Criteria.model_validate(
        {"where": {"field": "size", "operator": "=", "value": 0}}
    )

    assert store.count(Things, narrow).value == 5
    assert store.group_by(Things, ["size"], narrow) == [{"size": 0, "count": 5}]
    assert store.measures(Things, narrow, biggest=("max", "size")) == {"biggest": 0}


def test_an_operator_with_no_sql_is_refused_rather_than_guessed():
    """Context: unreachable through a validated criteria — every operator the model publishes
    has a translation. It is here so that adding one without a translation fails loudly on its
    first use instead of building a clause that means something else."""

    condition = Condition.model_construct(
        field="size", value=1, operator="nope"  # skips validation, as a new operator would
    )

    with pytest.raises(InvalidCriteria, match="no SQL translation"):
        where_clause(Thing, condition)


def test_the_column_types_convert_in_both_directions():
    """Context: the dialect is part of SQLAlchemy's signature and these decorators ignore it —
    every conversion here is the same on any engine."""
    dialect = SQLiteDialect()

    plain = JsonText()
    assert plain.process_bind_param(["age"], dialect) == '["age"]'
    assert plain.process_result_value('["age"]', dialect) == ["age"]
    assert plain.process_result_value(None, dialect) is None

    words = TranslatedText()
    stored = words.process_bind_param({"default": "Año", "en": "Year"}, dialect)
    assert stored == '{"default": "Año", "en": "Year"}'
    assert words.process_result_value(stored, dialect) == {"default": "Año", "en": "Year"}
    assert words.process_bind_param(None, dialect) is None
    with pytest.raises(ValueError, match="default"):
        words.process_bind_param({"en": "Year"}, dialect)


def test_every_type_reads_the_text_a_url_carries():
    assert FieldType.NUMBER.read("1.5") == 1.5
    assert FieldType.DATE.read("2026-03-14").isoformat() == "2026-03-14"
    assert FieldType.TEXT.read("labs") == "labs"
    assert FieldType.INTEGER.read(1000) == 1000


def test_an_annotation_that_is_not_a_class_is_not_a_relation():
    """Context: `is_mapped` is handed whatever the annotation held, including things that are
    not classes at all — `list[str]` leaves `str`, a bare `Any` leaves `Any`."""
    from sincpro_framework.orm.sqlalchemy.model_introspection import is_mapped

    assert is_mapped(Thing) is True
    assert is_mapped("not a class") is False
    assert is_mapped(Operator.EQ) is False


def test_a_narrowed_count_reaches_the_ceiling_query(store, queries_run):
    """Context: the capped count with a WHERE — the branch a bare count never walks."""
    with queries_run() as statements:
        page = store.search(
            Things,
            Criteria(
                where=Condition(field="size", operator=Operator.GTE, value=0),
                pagination=Pagination(limit=2),
            ),
        )

    assert page.count is not None and page.count.value == 25
    assert len(statements) == 2


def test_the_exact_count_honours_the_filter_too(store):
    """Context: the capped count and the exact one are two statements, and only one of them
    is walked by default — a filter the exact branch ignored would answer the whole table."""
    narrow = Criteria(
        where=Condition(field="size", operator=Operator.EQ, value=0),
        count=CountMode.EXACT,
        pagination=Pagination(limit=1),
    )

    assert store.count(Things, narrow) == Count(value=5, exact=True)
    assert store.count(Things, Criteria(count=CountMode.EXACT)) == Count(value=25, exact=True)


def test_mapping_twice_changes_nothing_and_properties_rename_a_column():
    """Context: a context can be built more than once in one process, and a table that
    predates the convention maps its own column name onto the `Entity` attribute."""
    from dataclasses import dataclass

    from sqlalchemy import Column, Integer, MetaData, Table, Text
    from sqlalchemy.orm import registry

    from sincpro_framework.ddd.entity import Entity
    from sincpro_framework.orm.sqlalchemy.data_mapper import entity_table, map_aggregates

    @dataclass
    class Legacy(Entity):
        __label__ = {"default": "Legacy"}
        __labels__ = {"label": {"default": "Label"}}

        label: str

    @dataclass
    class Plain:
        plain_id: str

    own = registry()
    legacy_table = Table(
        "legacy",
        own.metadata,
        Column("legacy_id", Text, primary_key=True),
        Column("created_at", Text, nullable=False),
        Column("updated_at", Text),
        Column("version", Integer, nullable=False, default=1),
        Column("caption", Text, nullable=False),
    )
    plain_table = Table("plain", own.metadata, Column("plain_id", Text, primary_key=True))

    tables = {Legacy: legacy_table, Plain: plain_table}
    renamed = {Legacy: {"id": legacy_table.c.legacy_id}}
    map_aggregates(own, tables, properties=renamed)
    map_aggregates(own, tables, properties=renamed)

    assert {mapper.class_ for mapper in own.mappers} == {Legacy, Plain}
    assert Legacy.id.property.columns[0].name == "legacy_id"  # type: ignore[attr-defined]
    assert entity_table("fresh", MetaData(), Column("x", Text)).c.keys() == [
        "id",
        "created_at",
        "updated_at",
        "version",
        "x",
    ]
