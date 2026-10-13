"""The SQLAlchemy store, held to the same behaviours as the memory store (`extensions/`).

The mapping below is the only thing this file adds: which tables the classes live in. The
behaviours are written in `tests/data_layer/extensions/`, over plain dataclasses and the
repository's own vocabulary, and run on SQLite and, with `SINCPRO_POSTGRES_URL`, on PostgreSQL.
"""

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, Numeric, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm import Repository, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table

from ..extensions.answers import ExtendedAnswers
from ..extensions.classes import make_classes
from ..extensions.editing import DelegatedEdits, ExtendedEdits
from ..extensions.reading import ExtendedReads
from ..extensions.writing import ExtendedWrites
from .engines import fresh


class SqlWorld:
    @pytest.fixture(autouse=True)
    def _engine(self, engine_url):
        self.engine_url = engine_url

    def make_world(self, delegated: bool = False):
        k = make_classes()
        md = MetaData()
        reg = registry(metadata=md)
        move_t = entity_table(
            "move",
            md,
            Column("number", Text),
            Column("total", Numeric(12, 2), nullable=False),
        )

        def owned(name: str, parent: str, *columns):
            return Table(
                name,
                md,
                Column("id", Text, ForeignKey(f"{parent}.id"), primary_key=True),
                *columns,
            )

        map_aggregates(reg, {k.Move: move_t}, delegated=[k.Move] if delegated else [])
        map_aggregates(
            reg, {k.MoveDelivery: owned("move_delivery", "move", Column("route", Text))}
        )
        map_aggregates(
            reg,
            {
                k.MoveTracking: owned(
                    "move_tracking", "move_delivery", Column("carrier", Text)
                )
            },
        )
        map_aggregates(
            reg, {k.MoveFiscal: owned("move_fiscal", "move", Column("cufd", Text))}
        )
        return k, Repository(fresh(self.engine_url, md))


class TestReads(SqlWorld, ExtendedReads):
    pass


class TestWrites(SqlWorld, ExtendedWrites):
    pass


class TestAnswers(SqlWorld, ExtendedAnswers):
    pass


class TestEdits(SqlWorld, ExtendedEdits):
    pass


class TestDelegatedEdits(SqlWorld, DelegatedEdits):
    pass
