"""A frame with no rows still has its columns — so a join over it, its Arrow and its Parquet say
what it would hold — and a frame comes back from Arrow as it went."""

import io
from datetime import UTC, date, datetime
from decimal import Decimal

import duckdb
import pyarrow
import pyarrow.parquet

from sincpro_framework.data_analysis import DataFrame, QueryCache
from sincpro_framework.ddd import Criteria
from tests.data_analysis.conftest import CountingRepository, Line

NOTHING_POSTED = Criteria.model_validate(
    {"where": {"field": "state", "operator": "=", "value": "nowhere"}}
)


def test_a_read_with_no_rows_has_the_columns_of_the_aggregate_typed(
    repository: CountingRepository,
):
    frame = QueryCache().fetch_all(repository, Line, NOTHING_POSTED)

    assert len(frame) == 0 and frame.complete
    assert frame.columns == (
        "id",
        "created_at",
        "updated_at",
        "version",
        "journal",
        "state",
        "posted_at",
        "amount",
        "quantity",
    )
    kinds = dict(zip(frame.columns, frame.types))
    assert kinds["amount"] == "decimal" and kinds["posted_at"] == "date"
    assert kinds["quantity"] == "integer" and kinds["created_at"] == "datetime"


def test_a_read_with_no_rows_has_the_columns_its_specification_keeps(
    repository: CountingRepository,
):
    masked = Criteria.model_validate(
        {
            "where": {"field": "state", "operator": "=", "value": "nowhere"},
            "specification": {"journal": {}, "amount": {}},
        }
    )

    frame = QueryCache().fetch_all(repository, Line, masked)

    assert frame.columns == ("id", "journal", "amount")
    assert frame.types == ("string", "string", "decimal")


def test_an_empty_frame_goes_to_arrow_and_parquet_with_its_columns(
    repository: CountingRepository,
):
    frame = QueryCache().fetch_all(repository, Line, NOTHING_POSTED)

    table = frame.to_arrow()
    from_parquet = pyarrow.parquet.read_table(io.BytesIO(frame.to_parquet()))

    assert table.num_rows == 0 and table.column_names == list(frame.columns)
    assert table.schema.field("amount").type == pyarrow.decimal128(38, 0)
    assert from_parquet.column_names == list(frame.columns)


def test_duckdb_joins_over_an_empty_frame(repository: CountingRepository):
    lines = QueryCache().fetch_all(repository, Line, Criteria())
    none = QueryCache().fetch_all(repository, Line, NOTHING_POSTED)
    connection = duckdb.connect()
    connection.register("lines", lines.to_arrow())
    connection.register("none", none.to_arrow())

    joined = connection.sql(
        "SELECT lines.id, none.state FROM lines LEFT JOIN none ON none.id = lines.id"
    ).fetchall()

    assert len(joined) == len(lines) and all(state is None for _, state in joined)


def test_a_frame_comes_back_from_arrow_as_it_went():
    frame = DataFrame.from_rows(
        [
            {
                "id": "a",
                "amount": Decimal("10.50"),
                "at": datetime(2026, 9, 27, 12, tzinfo=UTC),
                "on": date(2026, 9, 27),
                "count": 3,
                "ratio": 0.5,
                "paid": True,
                "note": None,
            }
        ]
    )

    back = DataFrame.from_arrow(frame.to_arrow())

    assert back.columns == frame.columns
    assert back.types == frame.types
    assert back.rows() == frame.rows()


def test_an_arrow_table_with_no_rows_keeps_its_columns():
    table = pyarrow.table(
        {"id": pyarrow.array([], pyarrow.string()), "n": pyarrow.array([], pyarrow.int32())}
    )

    frame = DataFrame.from_arrow(table)

    assert frame.columns == ("id", "n") and frame.types == ("string", "integer")
    assert len(frame) == 0
