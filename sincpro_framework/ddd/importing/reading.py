"""What a file holds, as rows: CSV, JSON, JSON Lines or an Excel sheet, read the same way.

    rows_of(b"name,country_id/@key\\nACME,base.bo\\n", ImportFormat.CSV)
        ->  ImportRow(at=(2,), cells={"name": "ACME", "country_id": {"@key": "base.bo"}})

Context: a flat file names a nested value by its path — `country_id/@key`, `lines/quantity` — so a
row of any format comes out the way JSON already writes it, and what follows reads one shape.
Every reader is a stream: the file is decoded once and rows are handed on as they are read. The
standard library reads them all; nothing here knows an entity.
"""

import csv
import io
import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.importing.excel import sheet_rows

DELIMITERS = ",;\t|"
"""What separates the columns of a CSV: whichever of these its header line holds most."""


class ImportFormat(StrEnum):
    """What the content of an import is written in."""

    CSV = "csv"
    """One record per line, the first line its header. The delimiter is read off the header."""
    JSON = "json"
    """An array of objects, nested as the records are."""
    JSON_LINES = "jsonl"
    """One object per line."""
    EXCEL = "xlsx"
    """One sheet of a workbook, the first row its header."""


@dataclass
class ImportRow:
    """One row of the file: where it is, and its cells as nested values."""

    at: tuple[str | int, ...]
    """Its position: `(12,)` the line of a CSV, `("Partners", 12)` a sheet's row, `(3,)` the item
    of a JSON array."""
    cells: dict[str, Any]


def nested(cells: Mapping[str, Any]) -> dict[str, Any]:
    """The cells of a flat row, each header read as a path.

        nested({"name": "ACME", "country_id/@key": "base.bo", "lines/quantity": 2})
            ->  {"name": "ACME", "country_id": {"@key": "base.bo"}, "lines": {"quantity": 2}}

    A path into a list (`lines/quantity`) is a mapping here: whether it is one child or the
    references a cell lists is the entity's to say, when the row is read as a record.
    """
    values: dict[str, Any] = {}
    for header, cell in cells.items():
        *parents, last = header.split("/")
        level = values
        for one in parents:
            level = level.setdefault(one, {})
        level[last] = cell
    return values


def _renamed(header: list[Any], columns: Mapping[str, str | None]) -> list[str | None]:
    """The header with each column renamed or dropped as `columns` says, blanks dropped."""
    renamed: list[str | None] = []
    for one in header:
        name = "" if one is None else str(one).strip()
        renamed.append(columns.get(name, name) or None)
    return renamed


def _flat(
    header: list[str | None], lines: Iterable[tuple[tuple[str | int, ...], list[Any]]]
) -> Iterator[ImportRow]:
    """Each line of a flat file as a row of nested cells, by its header."""
    for at, values in lines:
        cells = {name: value for name, value in zip(header, values) if name is not None}
        yield ImportRow(at, nested(cells))


def _text(content: bytes, encoding: str) -> str:
    """The content as text, refused naming the encoding it was read in."""
    try:
        return content.decode(encoding)
    except UnicodeDecodeError as error:
        raise exceptions.InvalidValue(
            f'the content is not {encoding} text: name its encoding (`encoding="cp1252"`)',
            (),
            "content",
        ) from error


def _csv_rows(text: str, columns: Mapping[str, str | None]) -> Iterator[ImportRow]:
    """The lines of a CSV after its header, the delimiter read off the header line."""
    first = text.split("\n", 1)[0]
    delimiter = (
        max(DELIMITERS, key=first.count) if any(one in first for one in DELIMITERS) else ","
    )
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header = _renamed(next(reader, []), columns)

    def lines() -> Iterator[tuple[tuple[str | int, ...], list[Any]]]:
        for values in reader:
            if any(one.strip() for one in values):
                yield (reader.line_num,), list(values)

    return _flat(header, lines())


def _json_rows(
    items: Iterable[tuple[int, Any]], columns: Mapping[str, str | None]
) -> Iterator[ImportRow]:
    """Each object of a JSON array or JSON Lines, its top-level names renamed as `columns` says."""
    for index, item in items:
        if not isinstance(item, dict):
            raise exceptions.InvalidValue(
                f"each item is a record, as a JSON object; item {index} is {type(item).__name__}",
                (index,),
                "content",
            )
        cells = {columns.get(name, name): value for name, value in item.items()}
        yield ImportRow((index,), {name: value for name, value in cells.items() if name})


def _json_items(text: str, lines: bool) -> Iterator[tuple[int, Any]]:
    """The items of a JSON array, or of one JSON object per line, each with its position."""
    try:
        if not lines:
            items = json.loads(text)
            if not isinstance(items, list):
                raise exceptions.InvalidValue(
                    "the content is a JSON array of records", (), "content"
                )
            yield from enumerate(items)
            return
        for number, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                yield number, json.loads(line)
    except json.JSONDecodeError as error:
        raise exceptions.InvalidValue(
            f"the content is not JSON: {error.msg} at line {error.lineno}", (), "content"
        ) from error


def rows_of(
    content: bytes,
    format: ImportFormat,
    sheet: str = "",
    columns: Mapping[str, str | None] | None = None,
    encoding: str = "utf-8-sig",
) -> Iterator[ImportRow]:
    """Every row of the content, in the file's order: a flat file by its header, renamed or
    dropped as `columns` says (`{"Razón social": "name", "Notas": None}`), a JSON record as it is.

        rows_of(b"name;vat\\nACME;102\\n", ImportFormat.CSV)
            ->  ImportRow(at=(2,), cells={"name": "ACME", "vat": "102"})
        rows_of(b'[{"name": "ACME"}]', ImportFormat.JSON)
            ->  ImportRow(at=(0,), cells={"name": "ACME"})
        rows_of(workbook, ImportFormat.EXCEL, sheet="Partners")
            ->  ImportRow(at=("Partners", 2), cells={"name": "ACME", "vat": 1020304050})

    A blank line or row is skipped. Content that does not read as its format is refused as an
    `InvalidValue` on `content`.
    """
    renames = columns or {}
    match format:
        case ImportFormat.EXCEL:
            title, lines = sheet_rows(content, sheet)
            first = next(lines, None)
            if first is None:
                return iter(())
            header = _renamed(first[1], renames)
            return _flat(header, (((title, number), values) for number, values in lines))
        case ImportFormat.CSV:
            return _csv_rows(_text(content, encoding), renames)
        case ImportFormat.JSON | ImportFormat.JSON_LINES:
            text = _text(content, encoding)
            return _json_rows(_json_items(text, format == ImportFormat.JSON_LINES), renames)
