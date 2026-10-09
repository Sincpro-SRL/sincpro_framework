"""The parity cases, answered by the database.

`tests/ddd/criteria/criteria-parity.json` holds the cases every evaluator of the filter language
answers the same way: the in-memory one (`test_criteria_parity.py`) and the TypeScript client
copy it. This runs the same file through the SQL translator, the evaluator production reads
with, so a case written once holds all three to one answer. The rows and the expectations are
read from the file and never repeated here: a case added there is checked here.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Column, DateTime, Integer, MetaData, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd.criteria import Criteria, Pagination, expression_from
from sincpro_framework.orm.sqlalchemy.domain.custom_fields import JsonText
from sincpro_framework.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.orm.sqlalchemy.services.data_mapper import map_aggregates

from .engines import fresh

PARITY = json.loads(
    (Path(__file__).parents[1] / "ddd" / "criteria" / "criteria-parity.json").read_text()
)


@dataclass
class ParityRow:
    """The row of the parity file, mapped: a text, a number, a nullable column, a list column
    and a date. The file writes the date as JSON carries one; the table keeps a date, and the
    case asks it with the ISO text a client sends."""

    id: str
    name: str
    size: int
    tags: list[str] = field(default_factory=list)
    owner: str | None = None
    made_at: datetime = datetime(2026, 1, 1)


parity_registry = registry(metadata=MetaData())
parity_table = Table(
    "criteria_parity_row",
    parity_registry.metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("size", Integer, nullable=False),
    Column("tags", JsonText, nullable=False),
    Column("owner", Text, nullable=True),
    Column("made_at", DateTime, nullable=False),
)
map_aggregates(parity_registry, {ParityRow: parity_table})


@pytest.fixture
def rows(engine_url: str) -> Repository:
    store = Repository(fresh(engine_url, parity_registry.metadata))
    with store.context() as repository:
        for row in PARITY["rows"]:
            made_at = datetime.fromisoformat(row["made_at"]).replace(tzinfo=None)
            repository.save(ParityRow(**{**row, "made_at": made_at}))
    return store


@pytest.mark.parametrize("case", PARITY["cases"], ids=lambda case: case["name"])
def test_the_database_answers_each_parity_case_as_the_file_says(
    rows: Repository, case: dict[str, Any]
):
    where = None if case["where"] is None else expression_from(case["where"])
    everything = Pagination(limit=len(PARITY["rows"]) + 1)

    found = rows.search(ParityRow, Criteria(where=where, pagination=everything))

    assert sorted(one.id for one in found) == sorted(case["expected"])
