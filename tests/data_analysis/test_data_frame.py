"""`DataFrame`: rows held by column, merged page by page, shipped as Parquet or Arrow, and handed
to pandas, polars or DuckDB as they are — decimals exact all the way."""

import io
from datetime import UTC, date, datetime
from decimal import Decimal

import duckdb
import pandas
import polars
import pyarrow
import pyarrow.ipc
import pyarrow.parquet
import pytest

from sincpro_framework.data_analysis import DataFrame, QueryCache, SchemaMismatch
from tests.data_analysis.conftest import CountingRepository, Line, criteria


def _frame(repository: CountingRepository) -> DataFrame:
    return QueryCache().fetch_all(repository, Line, criteria(limit=500))


def test_appending_a_page_keeps_every_row_once():
    first = DataFrame.from_rows([{"id": "a", "v": 1}, {"id": "b", "v": 2}])
    page = DataFrame.from_rows([{"id": "b", "v": 2}, {"id": "c", "v": 3}])

    merged = first.append(page)

    assert merged.column("id") == ["a", "b", "c"]


def test_a_page_of_another_shape_is_refused():
    first = DataFrame.from_rows([{"id": "a", "v": 1}])

    with pytest.raises(SchemaMismatch, match="w"):
        first.append(DataFrame.from_rows([{"id": "b", "w": 1}]))


def test_sort_and_select_are_there_and_the_rest_goes_to_a_dataframe_library(
    repository: CountingRepository,
):
    frame = _frame(repository)

    top = frame.sort([("amount", "desc"), ("id", "asc")]).select(["id", "amount"])

    assert top.columns == ("id", "amount")
    assert top.column("amount")[0] == max(frame.column("amount"))


def test_parquet_and_arrow_round_trip_with_decimals_exact(repository: CountingRepository):
    frame = _frame(repository)

    from_parquet = pyarrow.parquet.read_table(io.BytesIO(frame.to_parquet()))
    from_ipc = pyarrow.ipc.open_stream(frame.to_ipc()).read_all()

    assert from_parquet.num_rows == from_ipc.num_rows == len(frame)
    assert from_parquet.column("amount").to_pylist() == frame.column("amount")
    assert isinstance(frame.column("amount")[0], Decimal)


def test_pandas_polars_and_duckdb_take_it_as_it_is(repository: CountingRepository):
    frame = _frame(repository)

    in_polars = polars.DataFrame(frame)
    in_pandas = frame.to_arrow().to_pandas()
    in_duckdb = (
        duckdb.from_arrow(frame.to_arrow()).aggregate("journal, count(*) AS n").fetchall()
    )

    assert in_polars.height == len(in_pandas) == len(frame)
    assert sum(n for _, n in in_duckdb) == len(frame)
    assert isinstance(in_pandas, pandas.DataFrame)


def test_the_json_answer_is_by_column_with_decimals_as_text():
    frame = DataFrame.from_rows([{"id": "a", "amount": Decimal("10.50")}])

    assert frame.to_json()["values"] == [["a"], ["10.50"]]
    assert frame.to_json()["types"] == ["string", "decimal"]


def test_arrow_keeps_the_timezone_of_aware_datetimes_and_a_column_with_no_value_is_null():
    frame = DataFrame.from_rows(
        [{"id": "a", "at": datetime(2026, 9, 27, 12, tzinfo=UTC), "note": None}]
    )

    schema = frame.to_arrow().schema

    assert schema.field("at").type == pyarrow.timestamp("us", tz="UTC")
    assert schema.field("note").type == pyarrow.null()


def test_narrowing_on_a_column_the_frame_does_not_have_is_refused_naming_it():
    frame = DataFrame.from_rows([{"id": "a", "amount": Decimal("1")}])

    with pytest.raises(KeyError, match="nope is not a column"):
        frame.narrow({"field": "nope", "operator": "=", "value": 1})


def test_a_value_that_is_not_of_the_column_type_is_refused_naming_the_column():
    frame = DataFrame.from_rows([{"id": "a", "amount": Decimal("1")}])

    with pytest.raises(ValueError, match="amount: 'abc' is not a decimal"):
        frame.narrow({"field": "amount", "operator": "=", "value": "abc"})


def test_a_page_holds_each_row_once_even_when_it_repeats_one():
    frame = DataFrame.from_rows([]).append(
        DataFrame.from_rows([{"id": "a", "v": 1}, {"id": "a", "v": 1}])
    )

    assert frame.column("id") == ["a"]


@pytest.mark.parametrize(
    "condition, kept",
    [
        ({"field": "quantity", "operator": "=", "value": "3"}, ["b"]),
        ({"field": "quantity", "operator": ">", "value": "2"}, ["b"]),
        ({"field": "ratio", "operator": "<", "value": "1"}, ["a"]),
    ],
)
def test_a_value_written_as_text_is_read_as_the_column_type(condition: dict, kept: list):
    frame = DataFrame.from_rows(
        [{"id": "a", "quantity": 1, "ratio": 0.5}, {"id": "b", "quantity": 3, "ratio": 1.5}]
    )

    assert frame.narrow(condition).column("id") == kept


def test_a_column_of_dates_and_datetimes_is_of_datetimes_to_narrow_and_to_arrow():
    frame = DataFrame.from_rows(
        [{"id": "a", "at": date(2026, 1, 10)}, {"id": "b", "at": datetime(2026, 1, 20, 9)}]
    )

    later = frame.narrow({"field": "at", "operator": ">=", "value": "2026-01-15"})

    assert later.column("id") == ["b"]
    assert frame.column("at")[0] == datetime(2026, 1, 10)
    assert frame.to_arrow().num_rows == 2


def test_a_column_of_decimals_and_floats_is_of_numbers_to_arrow():
    frame = DataFrame.from_rows([{"id": "a", "v": Decimal("1.5")}, {"id": "b", "v": 2.25}])

    assert frame.to_arrow().column("v").to_pylist() == [1.5, 2.25]
