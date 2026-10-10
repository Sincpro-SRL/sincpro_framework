"""A frame as Arrow and Parquet — the `[data-analysis]` extra: pyarrow.

Context: a decimal keeps its value as `decimal128(38, scale)`, the scale being the largest among
the column's values, so it can differ between two reads; an aware datetime is an instant, kept in
UTC; a column with no value is null. Nothing else is decided here: the frame's own types say what
each column is.
"""

import io
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

PYARROW_MISSING = (
    "pyarrow is not installed — Parquet and Arrow need it. Install with: "
    "pip install sincpro-framework[data-analysis]"
)

try:
    import pyarrow
    import pyarrow.ipc
    import pyarrow.parquet
except ImportError as error:  # pragma: no cover - tests/core/test_core_without_extras.py
    raise ImportError(PYARROW_MISSING) from error

if TYPE_CHECKING:
    from sincpro_framework.data_layer.data_analysis.frame import DataFrame

ARROW_TYPES = {
    "integer": pyarrow.int64(),
    "number": pyarrow.float64(),
    "string": pyarrow.string(),
    "boolean": pyarrow.bool_(),
    "date": pyarrow.date32(),
    "datetime": pyarrow.timestamp("us"),
    "null": pyarrow.null(),
}


def _scale(values: tuple[Any, ...]) -> int:
    scales = [0]
    for value in values:
        if isinstance(value, Decimal):
            exponent = value.as_tuple().exponent
            scales.append(-exponent if isinstance(exponent, int) else 0)
    return max(scales)


def _arrow_type(kind: str, values: tuple[Any, ...]) -> Any:
    if kind == "decimal":
        return pyarrow.decimal128(38, _scale(values))
    if kind == "datetime" and any(
        isinstance(value, datetime) and value.tzinfo is not None for value in values
    ):
        return pyarrow.timestamp("us", tz="UTC")
    return ARROW_TYPES[kind]


def _as_text_when_mixed(kind: str, values: tuple[Any, ...]) -> list[Any]:
    """A column mixed enough to be text is sent as text."""
    if kind != "string":
        return list(values)
    return [
        value if value is None or isinstance(value, str) else str(value) for value in values
    ]


def arrow_table(frame: "DataFrame") -> Any:
    fields = [
        (name, _arrow_type(kind, values))
        for name, kind, values in zip(frame.columns, frame.types, frame.values)
    ]
    columns = [
        _as_text_when_mixed(kind, values) for kind, values in zip(frame.types, frame.values)
    ]
    return pyarrow.table(columns, schema=pyarrow.schema(fields))


def _frame_type(arrow_type: Any) -> str:
    """Context: what Arrow holds beyond the frame's types — a list, a struct, a map — is text
    to a frame, as it is when it comes from rows."""
    types = pyarrow.types
    if types.is_boolean(arrow_type):
        return "boolean"
    if types.is_integer(arrow_type):
        return "integer"
    if types.is_floating(arrow_type):
        return "number"
    if types.is_decimal(arrow_type):
        return "decimal"
    if types.is_timestamp(arrow_type):
        return "datetime"
    if types.is_date(arrow_type):
        return "date"
    if types.is_null(arrow_type):
        return "null"
    return "string"


def frame_columns(
    table: Any,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[Any, ...], ...]]:
    """A `pyarrow.Table` as a frame's columns, types and values — the schema says the types, so
    a table with no rows keeps them."""
    columns = tuple(table.column_names)
    kinds = tuple(_frame_type(one.type) for one in table.schema)
    values = tuple(tuple(table.column(name).to_pylist()) for name in columns)
    return columns, kinds, values


def parquet_bytes(frame: "DataFrame") -> bytes:
    buffer = io.BytesIO()
    pyarrow.parquet.write_table(arrow_table(frame), buffer)
    return buffer.getvalue()


def ipc_bytes(frame: "DataFrame") -> bytes:
    table = arrow_table(frame)
    buffer = io.BytesIO()
    with pyarrow.ipc.new_stream(buffer, table.schema) as stream:
        stream.write_table(table)
    return buffer.getvalue()
