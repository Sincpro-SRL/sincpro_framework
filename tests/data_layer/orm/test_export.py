"""Every record a criteria matches, out as Parquet, an Arrow stream or JSON Lines, a page at a
time, typed by the model and read from one snapshot.

Runs on every engine the suite has (`engine_url`).
"""

import io
import json
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum

import pyarrow
import pyarrow.ipc
import pyarrow.parquet
import pytest
from sqlalchemy import Column, Date, DateTime, Integer, Numeric, Text, text
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.data_analysis.export import (
    FORMATS,
    arrow_schema,
    exported,
    write_parquet,
)
from sincpro_framework.data_layer.orm import map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.domain.custom_fields import JsonText
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import (
    ModelIntrospection,
)
from sincpro_framework.ddd.criteria import Condition, Criteria, Operator, Specification
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.exceptions import ProgrammingError

from .engines import fresh


class Side(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


@dataclass
class Posting(Entity):
    account: str
    number: int
    amount: Decimal
    side: Side
    posted_on: date
    booked_at: datetime
    tags: list[str] = field(default_factory=list)
    memo: str | None = None


exports = registry()
posting_table = entity_table(
    "posting",
    exports.metadata,
    Column("account", Text, nullable=False),
    Column("number", Integer, nullable=False),
    Column("amount", Numeric(12, 2), nullable=False),
    Column("side", Text, nullable=False),
    Column("posted_on", Date, nullable=False),
    Column("booked_at", DateTime(timezone=True), nullable=False),
    Column("tags", JsonText, nullable=False),
    Column("memo", Text),
)
map_aggregates(exports, {Posting: posting_table})

BOOKED = datetime(2026, 11, 6, 14, 30, tzinfo=timezone.utc)


@pytest.fixture
def ledger(engine_url) -> Generator[Repository]:
    database = fresh(engine_url, exports.metadata)
    repository = Repository(database)
    with repository.context() as unit:
        unit.session.add_all(
            [
                Posting(
                    account="caja" if n % 2 else "banco",
                    number=n,
                    amount=Decimal(f"{n}.25"),
                    side=Side.DEBIT if n % 3 else Side.CREDIT,
                    posted_on=date(2026, 11, n),
                    booked_at=BOOKED,
                    tags=["cierre"] if n == 1 else [],
                    memo=None if n % 4 else f"nota {n}",
                )
                for n in range(1, 13)
            ]
        )
    yield repository
    database.engine.dispose()


def read_parquet(data: bytes) -> pyarrow.Table:
    return pyarrow.parquet.read_table(io.BytesIO(data))


def test_the_schema_is_the_model_and_the_store_declares_the_scale():
    schema = arrow_schema(ModelIntrospection.describe(Posting), {"amount": 2})

    assert schema.names[0] == "id"
    assert schema.field("amount").type == pyarrow.decimal128(38, 2)
    assert schema.field("number").type == pyarrow.int64()
    assert schema.field("posted_on").type == pyarrow.date32()
    assert schema.field("booked_at").type == pyarrow.timestamp("us", tz="UTC")
    assert schema.field("tags").type == pyarrow.list_(pyarrow.string())
    assert schema.field("side").type == pyarrow.string()
    assert schema.field("memo").nullable and not schema.field("number").nullable


def test_an_exact_number_with_no_declared_scale_travels_as_its_exact_text():
    schema = arrow_schema(ModelIntrospection.describe(Posting))

    assert schema.field("amount").type == pyarrow.string()


def test_a_parquet_file_holds_every_record_typed_a_row_group_per_page(ledger):
    sink = io.BytesIO()

    written = write_parquet(sink, ledger, Posting, batch=5)

    table = read_parquet(sink.getvalue())
    metadata = pyarrow.parquet.ParquetFile(io.BytesIO(sink.getvalue())).metadata
    assert written == 12 and table.num_rows == 12
    assert metadata.num_row_groups == 3
    rows = sorted(table.to_pylist(), key=lambda row: row["number"])
    assert rows[0]["amount"] == Decimal("1.25")
    assert rows[0]["posted_on"] == date(2026, 11, 1)
    assert rows[0]["booked_at"] == BOOKED
    assert rows[0]["side"] == "debit" and rows[2]["side"] == "credit"
    assert rows[0]["tags"] == ["cierre"]
    assert rows[3]["memo"] == "nota 4" and rows[0]["memo"] is None


def test_the_criteria_narrows_what_is_exported_and_its_specification_the_columns(ledger):
    asked = Criteria(
        selection=Condition(field="account", value="caja"),
        specification=Specification({"number": Criteria(), "amount": Criteria()}),
    )

    answer = exported(ledger, Posting, asked, "parquet")
    table = read_parquet(b"".join(answer))

    assert table.column_names == ["id", "number", "amount"]
    assert sorted(table.column("number").to_pylist()) == [1, 3, 5, 7, 9, 11]


def test_an_arrow_stream_is_a_message_per_page(ledger):
    answer = exported(
        ledger,
        Posting,
        Criteria(selection=Condition(field="number", operator=Operator.LTE, value=7)),
        "arrow",
        batch=3,
    )

    reader = pyarrow.ipc.open_stream(io.BytesIO(b"".join(answer)))
    batches = list(reader)
    assert answer.media_type == FORMATS["arrow"] == "application/vnd.apache.arrow.stream"
    assert [one.num_rows for one in batches] == [3, 3, 1]


def test_json_lines_are_each_record_as_an_answer_writes_it(ledger):
    answer = exported(ledger, Posting, None, "jsonl", batch=5)

    lines = [json.loads(line) for line in b"".join(answer).decode().splitlines()]
    first = min(lines, key=lambda one: one["number"])
    assert len(lines) == 12
    assert first["amount"] == "1.25"
    assert first["posted_on"] == "2026-11-01"
    assert answer.filename == "posting.jsonl"
    assert answer.media_type == "application/jsonl"


def test_nothing_is_read_until_the_first_chunk_is_asked_for(ledger):
    answer = exported(ledger, Posting, None, "parquet")

    with ledger.context() as unit:
        unit.session.execute(text("DELETE FROM posting WHERE number > 10"))

    assert read_parquet(b"".join(answer)).num_rows == 10
    assert answer.disposition() == {
        "Content-Disposition": 'attachment; filename="posting.parquet"'
    }


def test_the_export_reads_one_snapshot_whatever_is_written_meanwhile(ledger, engine_url):
    if not engine_url.startswith("postgresql"):
        pytest.skip("another connection writing meanwhile is a server's question")
    answer = iter(exported(ledger, Posting, None, "jsonl", batch=5))

    first = next(answer)
    with ledger.database.engine.begin() as connection:
        connection.execute(text("DELETE FROM posting"))
    rest = b"".join(answer)

    assert len((first + rest).decode().splitlines()) == 12


def test_a_format_it_does_not_write_is_refused_before_anything_is_read(ledger):
    with pytest.raises(ProgrammingError, match="parquet, arrow, jsonl"):
        exported(ledger, Posting, None, "xlsx")


def test_an_export_under_an_offset_reads_every_page_after_it(ledger):
    from sincpro_framework.ddd.criteria import Offset, Pagination

    asked = Criteria(pagination=Pagination(limit=50, strategy=Offset(rows=2)))

    lines = b"".join(exported(ledger, Posting, asked, "jsonl", batch=3)).decode().splitlines()

    assert len(lines) == 10


def test_an_export_inside_another_unit_of_work_joins_it(ledger):
    with ledger.context() as unit:
        answer = b"".join(exported(unit, Posting, None, "jsonl"))

    assert len(answer.decode().splitlines()) == 12
