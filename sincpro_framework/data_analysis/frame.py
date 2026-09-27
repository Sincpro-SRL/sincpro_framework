"""`DataFrame`: the rows a query answered, held by column — merged page by page, narrowed without
asking again, and handed to whatever does the rest.

    sales = cache.fetch_all(repository, InvoiceLine, posted)       # read once
    this_month = sales.narrow({"field": "posted_at", "operator": ">=", "value": "2026-09-01"})
    this_month.to_parquet()                                        # to a client, a fraction of JSON
    polars.DataFrame(this_month)                                   # or pandas, DuckDB, pyarrow

Context: a frame knows the filter it answers and whether it holds every row of it. Only a
complete frame answers a narrower filter — the filter it holds and one more condition — because
from a frame that stopped at a page there is no telling which rows are missing. Beyond `narrow`,
`sort` and `select`, a frame computes nothing: it is handed, as Arrow, to a library that does.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Literal

from sincpro_framework.ddd.criteria import Criteria, holds
from sincpro_framework.ddd.criteria.criteria import All, Any_, Condition, Expression, Not

WIDER = {
    frozenset({"integer", "number"}): "number",
    frozenset({"integer", "decimal"}): "decimal",
    frozenset({"number", "decimal"}): "number",
    frozenset({"integer", "number", "decimal"}): "number",
    frozenset({"date", "datetime"}): "datetime",
}

type Direction = Literal["asc", "desc"]


class NotComplete(Exception):
    """A frame that stopped at a page was asked something only every row can answer."""


class SchemaMismatch(Exception):
    """A page of another shape was appended to a frame."""


def type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    return "string"


def _column_type(values: Sequence[Any]) -> str:
    """The one type that holds every value of the column — integers and floats are numbers,
    dates and datetimes are datetimes, anything else mixed is text."""
    kinds = {type_of(value) for value in values if value is not None}
    if not kinds:
        return "null"
    if len(kinds) == 1:
        return next(iter(kinds))
    return WIDER.get(frozenset(kinds), "string")


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _widened(values: Sequence[Any], kind: str) -> tuple[Any, ...]:
    """The column's values as its one type — a date among datetimes is a datetime at midnight,
    a decimal among floats is a float, an integer among decimals is a decimal."""
    match kind:
        case "datetime":
            return tuple(
                datetime.combine(value, time()) if type_of(value) == "date" else value
                for value in values
            )
        case "number":
            return tuple(
                float(value) if type_of(value) == "decimal" else value for value in values
            )
        case "decimal":
            return tuple(
                Decimal(value) if type_of(value) == "integer" else value for value in values
            )
    return tuple(values)


def _as_type_of(value: Any, kind: str, sample: Any) -> Any:
    """A condition's value as JSON wrote it, read as the column's type — `"3"` as an integer, an
    ISO date as a date, a number as a decimal — the way the repository would read it."""
    if isinstance(value, list):
        return [_as_type_of(one, kind, sample) for one in value]
    if kind == "decimal" and type_of(value) in ("integer", "number", "string"):
        return Decimal(str(value))
    if not isinstance(value, str):
        return value
    match kind:
        case "integer":
            return int(value)
        case "number":
            return float(value)
        case "date":
            return date.fromisoformat(value)
        case "datetime":
            moment = datetime.fromisoformat(value)
            if moment.tzinfo is None and sample.tzinfo is not None:
                return moment.replace(tzinfo=UTC)
            return moment
    return value


def _coerced(
    expression: Expression, kinds: Mapping[str, str], samples: Mapping[str, Any]
) -> Expression:
    match expression:
        case Condition():
            if expression.field not in kinds:
                raise KeyError(
                    f"{expression.field} is not a column — the columns are {', '.join(kinds)}"
                )
            kind = kinds[expression.field]
            try:
                value = _as_type_of(expression.value, kind, samples[expression.field])
            except (ArithmeticError, ValueError) as error:
                raise ValueError(
                    f"{expression.field}: {expression.value!r} is not a {kind}"
                ) from error
            return Condition(
                field=expression.field, operator=expression.operator, value=value
            )
        case All():
            return All(all=[_coerced(one, kinds, samples) for one in expression.all])
        case Any_():
            return Any_(any=[_coerced(one, kinds, samples) for one in expression.any])
        case Not():
            return Not(negate=_coerced(expression.negate, kinds, samples))


@dataclass(frozen=True)
class DataFrame:
    columns: tuple[str, ...]
    types: tuple[str, ...]
    values: tuple[tuple[Any, ...], ...]
    """One tuple per column, all of the same length."""

    key: str = "id"
    """The column that tells two rows apart — a page appended twice adds nothing."""

    where: dict[str, Any] | None = None
    """The filter every row answers, as `Criteria` writes it."""

    complete: bool = True
    """Whether every row the filter answers is here."""

    cursor: str | None = None
    """Where the next page starts, when it is not complete."""

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[Mapping[str, Any]],
        key: str = "id",
        where: dict[str, Any] | None = None,
        complete: bool = True,
        cursor: str | None = None,
    ) -> "DataFrame":
        """Columns in the order they first appear; a row without a column has `None` in it."""
        held = list(rows)
        columns: dict[str, None] = {}
        for row in held:
            columns.update(dict.fromkeys(row))
        names = tuple(columns)
        raw = [[row.get(name) for row in held] for name in names]
        types = tuple(_column_type(column) for column in raw)
        values = tuple(_widened(column, kind) for column, kind in zip(raw, types))
        return cls(names, types, values, key, where, complete, cursor)

    def __len__(self) -> int:
        return len(self.values[0]) if self.values else 0

    def _require(self, name: str) -> None:
        if name not in self.columns:
            raise KeyError(
                f"{name} is not a column — the columns are {', '.join(self.columns)}"
            )

    def column(self, name: str) -> list[Any]:
        self._require(name)
        return list(self.values[self.columns.index(name)])

    def rows(self) -> list[dict[str, Any]]:
        return (
            [dict(zip(self.columns, row)) for row in zip(*self.values)] if self.values else []
        )

    @property
    def version(self) -> str:
        """The frame's content as a key — the same rows, the same version: a strong ETag."""
        body = json.dumps(self.to_json(), sort_keys=True).encode()
        return hashlib.sha256(body).hexdigest()[:16]

    def _with_rows(
        self, rows: list[dict[str, Any]], where: dict[str, Any] | None
    ) -> "DataFrame":
        """These rows, with this frame's columns and types."""
        values = tuple(tuple(row[name] for row in rows) for name in self.columns)
        return replace(self, values=values, where=where)

    def append(self, page: "DataFrame") -> "DataFrame":
        """This frame and the next page of its query — each row once, by `key` — and what the page
        says about the rest: its cursor, and whether it was the last."""
        if len(self) and len(page) and page.columns != self.columns:
            missing = sorted(set(self.columns) ^ set(page.columns))
            raise SchemaMismatch(
                f"the page has other columns than the frame: {', '.join(missing)}"
            )
        if self.key not in page.columns:
            added = page.rows()
        else:
            held = set(self.column(self.key)) if self.key in self.columns else set()
            added = []
            for row in page.rows():
                if row[self.key] not in held:
                    held.add(row[self.key])
                    added.append(row)
        merged = DataFrame.from_rows(
            self.rows() + added, self.key, self.where, page.complete, page.cursor
        )
        return (
            merged
            if len(merged)
            else replace(self, complete=page.complete, cursor=page.cursor)
        )

    def narrow(self, where: dict[str, Any]) -> "DataFrame":
        """The rows that also answer `where` — with no read, since every row is here. Refused on
        a frame that stopped at a page."""
        if not self.complete:
            raise NotComplete(
                f"this frame holds {len(self)} rows of a read that has more — fetch_all it before "
                "narrowing, or read the narrower filter as its own query"
            )
        expression = Criteria.model_validate({"where": where}).expression
        rows = self.rows()
        kinds = dict(zip(self.columns, self.types))
        samples = {
            name: next((one for one in column if one is not None), None)
            for name, column in zip(self.columns, self.values)
        }
        condition = _coerced(expression, kinds, samples) if expression is not None else None
        kept = [row for row in rows if holds(condition, row.get)]
        combined = {"all": [self.where, where]} if self.where else where
        return self._with_rows(kept, combined)

    def sort(self, keys: Sequence[tuple[str, Direction]]) -> "DataFrame":
        """Ordered by `keys` — `null` last in both directions."""
        ordered = self.rows()
        for name, direction in reversed(keys):
            self._require(name)
            if direction == "asc":
                ordered.sort(key=lambda row, f=name: (row[f] is None, row[f]))
            else:
                ordered.sort(
                    key=lambda row, f=name: (row[f] is not None, row[f]), reverse=True
                )
        return self._with_rows(ordered, self.where)

    def select(self, columns: Sequence[str]) -> "DataFrame":
        for name in columns:
            self._require(name)
        positions = [self.columns.index(name) for name in columns]
        return replace(
            self,
            columns=tuple(columns),
            types=tuple(self.types[one] for one in positions),
            values=tuple(self.values[one] for one in positions),
        )

    def to_json(self) -> dict[str, Any]:
        """By column — smaller than an object per row — with a decimal as text and `types`
        saying it is one, so a total is never rounded on its way to a screen."""
        return {
            "columns": list(self.columns),
            "types": list(self.types),
            "values": [[_json_value(value) for value in column] for column in self.values],
        }

    def to_arrow(self) -> Any:
        """A `pyarrow.Table` — decimals as `decimal128(38, scale)`.

        Context: pyarrow is imported here, when it is asked for — a service that never ships
        Arrow never installs it."""
        from sincpro_framework.data_analysis.arrow import arrow_table

        return arrow_table(self)

    def __arrow_c_stream__(self, requested_schema: Any = None) -> Any:
        """The Arrow PyCapsule stream — how polars, pandas, DuckDB and pyarrow take a frame as
        it is: `polars.DataFrame(frame)`."""
        return self.to_arrow().__arrow_c_stream__(requested_schema)

    def to_parquet(self) -> bytes:
        """The frame as a Parquet file — to download, to cache, to keep."""
        from sincpro_framework.data_analysis.arrow import parquet_bytes

        return parquet_bytes(self)

    def to_ipc(self) -> bytes:
        """The frame as an Arrow IPC stream — `application/vnd.apache.arrow.stream` — for a client
        that appends page after page."""
        from sincpro_framework.data_analysis.arrow import ipc_bytes

        return ipc_bytes(self)
