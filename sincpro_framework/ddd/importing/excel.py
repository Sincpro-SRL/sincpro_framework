"""A workbook's rows, read with the standard library: an `.xlsx` file is a zip archive of XML parts,
and the values of one sheet are all an import needs.

    for row_number, values in sheet_rows(content, "Partners"):
        ...                                    (1, ["name", "vat"]), (2, ["ACME SRL", 1020304050])

Context: a cell holds text (in the shared strings, or inline), a number, a boolean or an error.
A date is a number whose style formats it as a date, so the styles are read to tell the two
apart, and the workbook's epoch (1900, or 1904 on old Macs) turns the number into a `datetime`.
The sheet is read as a stream, so memory stays flat whatever its size. Formats, formulas and
merged cells are left out: the import reads what each cell shows, not how it is drawn.
"""

import io
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any
from xml.etree.ElementTree import Element, fromstring, iterparse

from sincpro_framework.ddd import exceptions

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RELATIONSHIPS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
RELATIONSHIP_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

DATE_FORMAT_IDS = frozenset({14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47})
"""The built-in number formats (ECMA-376 §18.8.30) that show a date or a time."""

DATE_CODE = re.compile(r"[dmyhs]", re.IGNORECASE)
"""A custom number format shows a date when, its literal text aside, it has a date or time part."""

LITERAL = re.compile(r'"[^"]*"|\\.|\[[^\]]*\]')
"""What a number format writes as it is: quoted text, an escaped character, a colour or locale."""


def _text(element: Element) -> str:
    """The text of a string item: one `<t>`, or the runs of a rich text, phonetic hints left out."""
    plain = element.find(f"{MAIN}t")
    if plain is not None:
        return plain.text or ""
    return "".join(run.findtext(f"{MAIN}t") or "" for run in element.iter(f"{MAIN}r"))


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """Every text the cells point at by index, in order; none when the workbook holds no text."""
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    strings: list[str] = []
    for _, element in iterparse(archive.open("xl/sharedStrings.xml")):
        if element.tag == f"{MAIN}si":
            strings.append(_text(element))
            element.clear()
    return strings


def _date_styles(archive: zipfile.ZipFile) -> frozenset[int]:
    """The style indexes whose number format shows a date: the built-in ones, and each custom
    format with a day, month, year, hour or second outside its literal text."""
    if "xl/styles.xml" not in archive.namelist():
        return frozenset()
    styles = fromstring(archive.read("xl/styles.xml"))
    dates = set(DATE_FORMAT_IDS)
    for one in styles.iter(f"{MAIN}numFmt"):
        if DATE_CODE.search(LITERAL.sub("", one.get("formatCode", ""))):
            dates.add(int(one.get("numFmtId", "0")))
    cells = styles.find(f"{MAIN}cellXfs")
    formats = [] if cells is None else cells.findall(f"{MAIN}xf")
    return frozenset(
        index for index, one in enumerate(formats) if int(one.get("numFmtId", "0")) in dates
    )


def _sheet(archive: zipfile.ZipFile, name: str) -> tuple[str, str, datetime]:
    """The sheet's name, the part that holds it and the workbook's epoch.

    1. The sheets in the workbook's order; the first one when no name is asked.
    2. The part each one is in, from the workbook's relationships.
    Final: a name the workbook does not have is refused, listing the ones it has.
    """
    workbook = fromstring(archive.read("xl/workbook.xml"))
    sheets = [
        (one.get("name", ""), one.get(RELATIONSHIP_ID, ""))
        for one in workbook.iter(f"{MAIN}sheet")
    ]
    chosen = (
        next((one for one in sheets if one[0] == name), None)
        if name
        else (sheets or [None])[0]
    )
    if chosen is None:
        raise exceptions.InvalidValue(
            f"the workbook has no sheet {name!r}: it has {', '.join(one[0] for one in sheets)}",
            (),
            "sheet",
        )
    links = fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    target = next(
        one.get("Target", "")
        for one in links.iter(f"{RELATIONSHIPS}Relationship")
        if one.get("Id") == chosen[1]
    )
    part = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    properties = workbook.find(f"{MAIN}workbookPr")
    old_mac = properties is not None and properties.get("date1904") in ("1", "true")
    return chosen[0], part, datetime(1904, 1, 1) if old_mac else datetime(1899, 12, 30)


def _column(reference: str) -> int:
    """The position of a cell's column, from its reference: `A7` → 0, `AB3` → 27."""
    number = 0
    for letter in reference:
        if letter.isdigit():
            break
        number = number * 26 + ord(letter.upper()) - 64
    return number - 1


def _value(cell: Element, strings: list[str], dates: frozenset[int], epoch: datetime) -> Any:
    """What one cell shows: its text, number, boolean, date or error; `None` when empty."""
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        inline = cell.find(f"{MAIN}is")
        return None if inline is None else _text(inline)
    raw = cell.findtext(f"{MAIN}v")
    if raw is None:
        return None
    if kind == "s":
        return strings[int(raw)]
    if kind == "b":
        return raw == "1"
    if kind in ("str", "e"):
        return raw
    if kind == "d":
        return datetime.fromisoformat(raw)
    if int(cell.get("s", "0")) in dates:
        return epoch + timedelta(days=float(raw))
    try:
        return int(raw)
    except ValueError:
        return float(raw)


def sheet_rows(content: bytes, name: str = "") -> tuple[str, Iterator[tuple[int, list[Any]]]]:
    """The name of the sheet read — `name`, or the first — and its rows, each as its number and
    its values by column; a row with no value is left out.

        sheet_rows(workbook)                ->  ("Partners", <(1, ["name", "vat"]), (2, [...]), …>)
        sheet_rows(workbook, "Missing")     ->  raises InvalidValue("the workbook has no sheet …")
        sheet_rows(b"not a zip")            ->  raises InvalidValue("the content is not an Excel …")
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as error:
        raise exceptions.InvalidValue(
            "the content is not an Excel workbook (.xlsx)", (), "content"
        ) from error
    strings = _shared_strings(archive)
    dates = _date_styles(archive)
    title, part, epoch = _sheet(archive, name)

    def rows() -> Iterator[tuple[int, list[Any]]]:
        values: list[Any] = []
        for _, element in iterparse(archive.open(part)):
            if element.tag == f"{MAIN}c":
                position = _column(element.get("r", "")) if element.get("r") else len(values)
                values.extend([None] * (position + 1 - len(values)))
                values[position] = _value(element, strings, dates, epoch)
            elif element.tag == f"{MAIN}row":
                if any(one is not None for one in values):
                    yield int(element.get("r", "0")), values
                values = []
                element.clear()

    return title, rows()
