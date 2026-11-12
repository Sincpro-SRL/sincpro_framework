"""How each engine reads a text without case or accents, beyond what the parity file can show.

The parity file runs on a database whose collation lowers every letter. These are the cases it
cannot reach: a `C` collation, whose `lower()` leaves `Ó` as it is; an engine the framework does not
fold for, which keeps the case-insensitive `like` it always had; and the letters written into the
statement, so an index over the same expression serves it.
"""

import pytest
from sqlalchemy import Column, ColumnElement, Integer, MetaData, Table, Text, event, select
from sqlalchemy.dialects import mysql

from sincpro_framework.data_layer.orm.sqlalchemy.services.sql_translator import clause_over
from sincpro_framework.ddd.criteria import Condition, Operator
from sincpro_framework.ddd.criteria.domain.accents import FROM_LETTERS

from .engines import fresh

metadata = MetaData()
document = Table(
    "folded_document",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text),
)
c_collated = Table(
    "c_collated_document",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text(collation="C")),
)


def like(table: Table, value: str) -> ColumnElement[bool]:
    clause = clause_over(
        lambda field: table.c[field],
        Condition(field="name", operator=Operator.LIKE, value=value),
    )
    assert clause is not None
    return clause


def test_an_engine_the_framework_does_not_fold_for_reads_like_lowercased():
    """MySQL and any engine with no folding of its own: `lower()`, as `like` read before accents,
    which leaves the accents to that engine's collation — never a function it does not have.
    """
    written = str(like(document, "impresion").compile(dialect=mysql.dialect()))

    assert written.startswith("lower(folded_document.name) LIKE lower(")
    assert "sincpro_fold" not in written


def test_an_uppercase_accent_is_found_under_the_c_collation(engine_url):
    if not engine_url.startswith("postgresql"):
        pytest.skip("the C collation is PostgreSQL's")
    database = fresh(engine_url, metadata)
    with database.engine.begin() as connection:
        connection.execute(
            c_collated.insert(), [{"id": 1, "name": "IMPRESIÓN"}, {"id": 2, "name": "otra"}]
        )

        found = connection.execute(
            select(c_collated.c.name).where(like(c_collated, "impresion"))
        ).scalars()

        assert list(found) == ["IMPRESIÓN"]


def test_the_letters_are_written_into_the_statement_so_an_index_can_serve_it(engine_url):
    """A bound parameter would make the expression differ from one an index was built on."""
    if not engine_url.startswith("postgresql"):
        pytest.skip("translate is written only for PostgreSQL and DuckDB")
    database = fresh(engine_url, metadata)
    sent: list[str] = []
    event.listen(database.engine, "before_cursor_execute", lambda *args: sent.append(args[2]))

    with database.engine.connect() as connection:
        connection.execute(select(document.c.id).where(like(document, "impresion")))

    assert FROM_LETTERS in sent[-1]
