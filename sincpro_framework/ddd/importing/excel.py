"""A workbook's rows, read and written with the standard library: an `.xlsx` file is a zip archive
of XML parts, and the values of one sheet are all an import needs — and all an export writes.

    for row_number, values in sheet_rows(content, "Partners"):
        ...                                    (1, ["name", "vat"]), (2, ["ACME SRL", 1020304050])

    with SheetWriter(sink, "Partners") as sheet:
        sheet.write([["name", "vat"], ["ACME SRL", "1020304050"]])     a page of rows at a time

Context: a cell holds text (in the shared strings, or inline), a number, a boolean or an error.
A date is a number whose style formats it as a date, so the styles are read to tell the two
apart, and the workbook's epoch (1900, or 1904 on old Macs) turns the number into a `datetime`.
The sheet is read as a stream, so memory stays flat whatever its size. Formats, formulas and
merged cells are left out: the import reads what each cell shows, not how it is drawn.

Written, each value is the cell that shows it: a text, a number, a boolean, a day or a moment
under a date style. A number Excel cannot hold exactly (more than 15 digits) and a moment with
its zone are written as their text, so they come back as they went.
"""

import io
import re
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import TracebackType
from typing import IO, Any, Self
from xml.etree.ElementTree import Element, fromstring, iterparse
from xml.sax.saxutils import escape, quoteattr

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
        (
            one.get("Target", "")
            for one in links.iter(f"{RELATIONSHIPS}Relationship")
            if one.get("Id") == chosen[1]
        ),
        "",
    )
    if not target:
        raise exceptions.InvalidValue(
            f"the workbook names sheet {chosen[0]!r} but holds no part for it", (), "sheet"
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


SHEET_ROWS = 1_048_576
"""The rows an Excel sheet holds."""

EXACT_DIGITS = 15
"""The digits an Excel number holds exactly: a longer one is written as its text."""

EPOCH = datetime(1899, 12, 30)
"""Day zero of the numbers a workbook counts days with."""

FIRST_DAY = datetime(1900, 3, 1)
"""The first day Excel shows as its number: before it, its calendar counts a 29 February 1900
that never was, and before 1900 it shows none."""

NOT_IN_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
"""The characters XML cannot hold: control characters a cell cannot show either."""

SPREADSHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PACKAGE = "http://schemas.openxmlformats.org/package/2006"
DOCUMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
TYPES = "application/vnd.openxmlformats-officedocument.spreadsheetml"

CONTENT_TYPES = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Types xmlns="{PACKAGE}/content-types">'
    f'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    f'<Default Extension="xml" ContentType="application/xml"/>'
    f'<Override PartName="/xl/workbook.xml" ContentType="{TYPES}.sheet.main+xml"/>'
    f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{TYPES}.worksheet+xml"/>'
    f'<Override PartName="/xl/styles.xml" ContentType="{TYPES}.styles+xml"/>'
    f"</Types>"
)
PACKAGE_RELATIONSHIPS = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{PACKAGE}/relationships">'
    f'<Relationship Id="rId1" Type="{DOCUMENT}/officeDocument" Target="xl/workbook.xml"/>'
    f"</Relationships>"
)
WORKBOOK_RELATIONSHIPS = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{PACKAGE}/relationships">'
    f'<Relationship Id="rId1" Type="{DOCUMENT}/worksheet" Target="worksheets/sheet1.xml"/>'
    f'<Relationship Id="rId2" Type="{DOCUMENT}/styles" Target="styles.xml"/>'
    f"</Relationships>"
)
STYLES = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<styleSheet xmlns="{SPREADSHEET}">'
    f'<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    f'<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    f'<fills count="2"><fill><patternFill patternType="none"/></fill>'
    f'<fill><patternFill patternType="gray125"/></fill></fills>'
    f'<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    f'<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    f'<cellXfs count="4">'
    f'<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    f'<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    f'<xf numFmtId="22" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    f'<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    f"</cellXfs>"
    f'<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    f"</styleSheet>"
)
"""Four cell styles: plain, a day (format 14), a moment (format 22) and the header, in bold."""

DAY_STYLE, MOMENT_STYLE, HEADER_STYLE = 1, 2, 3

SHEET_START = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<worksheet xmlns="{SPREADSHEET}">'
    f'<sheetViews><sheetView workbookViewId="0">'
    f'<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
    f"</sheetView></sheetViews><sheetData>"
)
"""The sheet up to its rows, the header frozen at the top."""

SHEET_END = "</sheetData></worksheet>"


def _letters(position: int) -> str:
    """The letters of a column, from its position: 0 → `A`, 27 → `AB`."""
    letters = ""
    position += 1
    while position:
        position, rest = divmod(position - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _text_cell(reference: str, text: str, style: int) -> str:
    """A cell holding `text` as it is, without the characters XML cannot hold."""
    styled = f' s="{style}"' if style else ""
    shown = escape(NOT_IN_XML.sub("", text))
    return f'<c r="{reference}"{styled} t="inlineStr"><is><t xml:space="preserve">{shown}</t></is></c>'


def _cell(reference: str, value: Any, style: int = 0) -> str:
    """One cell as the sheet holds it; nothing for an empty one.

    1. A boolean is a boolean, a day and a moment without a zone are numbers under a date style
       — from the first day Excel shows as one.
    2. A number is a number while Excel holds it exactly; a longer one, its text.
    3. Anything else is its text: a moment with its zone, a day before 1900 as ISO 8601.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return f'<c r="{reference}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, datetime) and value.tzinfo is None and value >= FIRST_DAY:
        days = (value - EPOCH) / timedelta(days=1)
        return f'<c r="{reference}" s="{MOMENT_STYLE}"><v>{days!r}</v></c>'
    if (
        isinstance(value, date)
        and not isinstance(value, datetime)
        and value >= FIRST_DAY.date()
    ):
        return f'<c r="{reference}" s="{DAY_STYLE}"><v>{(value - EPOCH.date()).days}</v></c>'
    if isinstance(value, int) and abs(value) < 10**EXACT_DIGITS:
        return f'<c r="{reference}"><v>{value}</v></c>'
    if isinstance(value, float) and value == value and abs(value) != float("inf"):
        return f'<c r="{reference}"><v>{value!r}</v></c>'
    if (
        isinstance(value, Decimal)
        and value.is_finite()
        and len(value.as_tuple().digits) <= EXACT_DIGITS
    ):
        return f'<c r="{reference}"><v>{value:f}</v></c>'
    if isinstance(value, (date, time)):
        return _text_cell(reference, value.isoformat(), style)
    return _text_cell(reference, str(value), style)


class SheetWriter:
    """A workbook of one sheet written into `sink` a page of rows at a time — never the whole in
    memory: the sheet is one compressed part, streamed. The first row is the header, in bold and
    frozen at the top.

        with SheetWriter(sink, "Partners") as sheet:
            sheet.write([["name", "since"], ["ACME SRL", date(2019, 3, 1)]])
            sheet.write([["Andes Ltda", None]])          None: an empty cell

    A sheet holds 1 048 576 rows: one more is refused, naming the format that holds any number.
    """

    def __init__(self, sink: IO[bytes] | io.RawIOBase, name: str) -> None:
        self.sink = sink
        self.name = re.sub(r"[\\/?*:\[\]]", " ", name)[:31] or "Sheet1"
        self.rows = 0
        self.letters: list[str] = []
        self._archive: zipfile.ZipFile | None = None
        self._part: IO[bytes] | None = None

    def __enter__(self) -> Self:
        archive = zipfile.ZipFile(self.sink, "w", zipfile.ZIP_DEFLATED, compresslevel=1)
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", PACKAGE_RELATIONSHIPS)
        archive.writestr(
            "xl/workbook.xml",
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{SPREADSHEET}" xmlns:r="{DOCUMENT}"><sheets>'
            f'<sheet name={quoteattr(self.name)} sheetId="1" r:id="rId1"/>'
            f"</sheets></workbook>",
        )
        archive.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELATIONSHIPS)
        archive.writestr("xl/styles.xml", STYLES)
        self._part = archive.open("xl/worksheets/sheet1.xml", "w", force_zip64=True)
        self._part.write(SHEET_START.encode())
        self._archive = archive
        return self

    def write(self, rows: Iterable[Sequence[Any]]) -> None:
        """The rows, after those written before — `None` for an empty cell."""
        if self._part is None:
            raise exceptions.ProgrammingError("a SheetWriter writes inside its `with` block")
        written: list[str] = []
        for values in rows:
            self.rows += 1
            if self.rows > SHEET_ROWS:
                raise exceptions.InvalidValue(
                    f"an Excel sheet holds {SHEET_ROWS} rows: export these as CSV",
                    (),
                    "format",
                )
            while len(self.letters) < len(values):
                self.letters.append(_letters(len(self.letters)))
            style = HEADER_STYLE if self.rows == 1 else 0
            cells = "".join(
                _cell(f"{self.letters[at]}{self.rows}", value, style)
                for at, value in enumerate(values)
            )
            written.append(f'<row r="{self.rows}">{cells}</row>')
        self._part.write("".join(written).encode())

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        if self._part is not None:
            self._part.write(SHEET_END.encode())
            self._part.close()
        if self._archive is not None:
            self._archive.close()
