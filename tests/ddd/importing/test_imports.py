"""An entity's records from a file, saved as a save saves them: references found by their keys,
children gathered from the rows below, every rule judged, and one unit of work for the file.

The store here is `MemoryRepository`; `tests/data_layer/orm/test_imports_sql.py` runs the same
import on SQLite.
"""

import io
import zipfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from sincpro_framework import Preview, UseFramework
from sincpro_framework.common.failures.problem import problem_of
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    Entity,
    EntityCollection,
    EntityID,
    EntityImports,
    ExternalKey,
    Import,
    ImportFormat,
    ImportMode,
    ResponseImport,
    check,
)
from sincpro_framework.ddd import exceptions as ddd_exceptions

WORKBOOK = Path(__file__).parent / "partners.xlsx"


@dataclass
class Country(Entity):
    code: str
    name: str = ""

    @classmethod
    def DEFAULT_GET_ID(cls) -> str:
        return "code"


@dataclass
class Partner(Entity):
    name: str
    vat: str = ""
    country_id: "EntityID[Country] | None" = None
    parent_id: "EntityID[Partner] | None" = None
    since: date | None = None
    credit: Decimal = Decimal("0")

    @check(lambda partner: partner.credit)
    def credit_is_not_negative(self) -> None:
        if self.credit < 0:
            raise ddd_exceptions.InvalidValue("credit cannot be negative")


@dataclass
class Product(Entity):
    code: str
    name: str = ""

    @classmethod
    def DEFAULT_GET_ID(cls) -> str:
        return "code"


@dataclass
class InvoiceLine(Entity):
    product_id: "EntityID[Product] | None" = None
    quantity: int = 0

    @check(lambda line: line.quantity)
    def sells_something(self) -> None:
        if self.quantity < 1:
            raise ddd_exceptions.InvalidValue("a line sells at least one unit")


@dataclass
class Invoice(Entity):
    number: str
    partner_id: "EntityID[Partner] | None" = None
    lines: EntityCollection[InvoiceLine] = field(default_factory=EntityCollection)

    @classmethod
    def DEFAULT_GET_ID(cls) -> str:
        return "number"


class CommandImportPartners(Import[Partner, ResponseImport]):
    pass


class CommandImportInvoices(Import[Invoice, ResponseImport]):
    pass


BOLIVIA = Country(code="BO", name="Bolivia")
PERU = Country(code="PE", name="Perú")
ACME = Partner(name="ACME SRL")


def bus_with(repository: MemoryRepository) -> UseFramework:
    bus = UseFramework("imports", log_after_execution=False)
    bus.add_dependency("repository", repository)

    @bus.feature([CommandImportPartners, CommandImportInvoices])
    class Imports(EntityImports[Partner]):
        pass

    return bus


def stored(repository: MemoryRepository, kind: type) -> list:
    return sorted(repository.fetch_all(kind), key=lambda one: getattr(one, "name", "") or "")


def csv(text: str) -> bytes:
    return text.strip().encode()


def test_a_csv_creates_each_record_with_its_references_found_by_their_natural_key():
    repository = MemoryRepository().add(BOLIVIA, PERU)
    done = bus_with(repository)(
        CommandImportPartners(
            content=csv("""
name,vat,country_id,since,credit
ACME SRL,1020304050,BO,2019-03-01,1500.75
Andes Ltda,,PE,,0
"""),
            format=ImportFormat.CSV,
        ),
        ResponseImport,
    )

    assert (done.created, done.updated, done.unchanged, done.skipped) == (2, 0, 0, [])
    acme, andes = stored(repository, Partner)
    assert (acme.vat, acme.country_id, acme.since, acme.credit) == (
        "1020304050",
        BOLIVIA.id,
        date(2019, 3, 1),
        Decimal("1500.75"),
    )
    assert (andes.country_id, andes.since) == (PERU.id, None)


def test_the_external_key_finds_the_record_again_so_importing_twice_creates_nothing_new():
    repository = MemoryRepository().add(BOLIVIA)
    bus = bus_with(repository)
    content = csv("""
@key,name,country_id
res_partner_42,ACME SRL,BO
""")

    first = bus(
        CommandImportPartners(content=content, format=ImportFormat.CSV, origin="odoo"),
        ResponseImport,
    )
    again = bus(
        CommandImportPartners(content=content, format=ImportFormat.CSV, origin="odoo"),
        ResponseImport,
    )
    renamed = bus(
        CommandImportPartners(
            content=content.replace(b"ACME SRL", b"ACME S.R.L."),
            format=ImportFormat.CSV,
            origin="odoo",
        ),
        ResponseImport,
    )

    assert (first.created, again.unchanged, renamed.updated) == (1, 1, 1)
    (partner,) = stored(repository, Partner)
    (key,) = repository.fetch_all(ExternalKey)
    assert partner.name == "ACME S.R.L."
    assert (key.origin, key.entity_type, key.entity_id, key.key) == (
        "odoo",
        "Partner",
        partner.id,
        "res_partner_42",
    )


def test_a_record_references_one_stored_or_earlier_in_the_file_by_its_external_key():
    repository = MemoryRepository()
    content = csv("""
@key,name,parent_id/@key
p-1,ACME SRL,
p-2,ACME Santa Cruz,p-1
""")

    done = bus_with(repository)(
        CommandImportPartners(content=content, format=ImportFormat.CSV), ResponseImport
    )

    assert done.created == 2
    acme, santa_cruz = stored(repository, Partner)
    assert santa_cruz.parent_id == acme.id


def test_a_reference_to_a_record_later_in_the_file_is_refused_naming_where_to_put_it():
    content = csv("""
@key,name,parent_id/@key
p-2,ACME Santa Cruz,p-1
p-1,ACME SRL,
""")

    with pytest.raises(ddd_exceptions.ImportRefused) as refused:
        bus_with(MemoryRepository())(
            CommandImportPartners(content=content, format=ImportFormat.CSV)
        )

    failures = refused.value.failures
    assert len(failures) == 1
    failure = failures[0]
    assert failure.path == (2, "parent_id")
    assert "no Partner has @key 'p-1', stored or earlier in the file" in str(failure)


def test_an_invalid_file_writes_nothing_and_names_every_invalid_cell_not_only_the_first():
    repository = MemoryRepository().add(BOLIVIA)
    content = csv("""
name,country_id,credit
ACME SRL,BO,10
Andes Ltda,XX,0
Sur SA,BO,-5
""")

    with pytest.raises(ddd_exceptions.ImportRefused) as refused:
        bus_with(repository)(
            CommandImportPartners(content=content, format=ImportFormat.CSV, batch_size=1)
        )

    problem = problem_of(refused.value)
    assert stored(repository, Partner) == []
    assert problem.status == 422
    assert [(one.pointer, one.rule) for one in problem.errors] == [
        ("#/content/3/country_id", None),
        ("#/content/4/credit", "Partner.credit_is_not_negative"),
    ]
    assert problem.errors[0].detail == "no Country has code 'XX'"


def test_skip_invalid_writes_the_valid_records_and_answers_the_ones_left_out():
    repository = MemoryRepository().add(BOLIVIA)
    content = csv("""
name,country_id
ACME SRL,BO
Andes Ltda,XX
""")

    done = bus_with(repository)(
        CommandImportPartners(content=content, format=ImportFormat.CSV, skip_invalid=True),
        ResponseImport,
    )

    assert done.created == 1
    assert [one.pointer for one in done.skipped] == ["#/content/3/country_id"]
    assert [one.name for one in stored(repository, Partner)] == ["ACME SRL"]


def test_a_header_that_names_no_field_refuses_the_whole_file_before_any_row():
    content = csv("""
name,colour
ACME SRL,red
""")

    with pytest.raises(ddd_exceptions.ImportRefused) as refused:
        bus_with(MemoryRepository())(
            CommandImportPartners(content=content, format=ImportFormat.CSV)
        )

    assert [str(one) for one in refused.value.failures] == ["Partner has no field colour"]
    assert problem_of(refused.value).errors[0].pointer == "#/content/header/colour"


def test_columns_rename_a_header_the_file_cannot_change_or_drop_it():
    repository = MemoryRepository()
    content = csv("""
Razón social,Notas
ACME SRL,a note nobody imports
""")

    bus_with(repository)(
        CommandImportPartners(
            content=content,
            format=ImportFormat.CSV,
            columns={"Razón social": "name", "Notas": None},
        )
    )

    assert [one.name for one in stored(repository, Partner)] == ["ACME SRL"]


def test_the_rows_below_a_record_with_blank_own_cells_are_its_children():
    repository = MemoryRepository().add(ACME, Product(code="SKU-7"), Product(code="SKU-9"))
    content = csv(f"""
number,partner_id,lines/product_id,lines/quantity
F-1,{ACME.id},SKU-7,2
,,SKU-9,1
F-2,{ACME.id},SKU-7,5
""")

    done = bus_with(repository)(
        CommandImportInvoices(content=content, format=ImportFormat.CSV), ResponseImport
    )

    assert done.created == 2
    first, second = sorted(repository.fetch_all(Invoice), key=lambda one: one.number)
    assert [line.quantity for line in first.lines] == [2, 1]
    assert [line.quantity for line in second.lines] == [5]


def test_a_child_that_breaks_a_rule_points_at_its_own_cell():
    repository = MemoryRepository().add(Product(code="SKU-7"))
    content = b'[{"number": "F-1", "lines": [{"product_id": "SKU-7", "quantity": 2}, {"product_id": "SKU-7", "quantity": 0}]}]'

    with pytest.raises(ddd_exceptions.ImportRefused) as refused:
        bus_with(repository)(CommandImportInvoices(content=content, format=ImportFormat.JSON))

    assert [one.pointer for one in problem_of(refused.value).errors] == [
        "#/content/0/lines/1/quantity"
    ]


def test_json_names_a_reference_by_any_of_its_keys():
    repository = MemoryRepository().add(BOLIVIA, PERU)
    content = b"""[
        {"@key": "p-1", "name": "ACME SRL", "country_id": "BO"},
        {"@key": "p-2", "name": "Andes", "country_id": {"code": "PE"}, "parent_id": {"@key": "p-1"}}
    ]"""

    bus_with(repository)(CommandImportPartners(content=content, format=ImportFormat.JSON))

    acme, andes = stored(repository, Partner)
    assert (andes.country_id, andes.parent_id) == (PERU.id, acme.id)


def test_json_lines_reads_one_record_per_line():
    repository = MemoryRepository()
    content = b'{"name": "ACME SRL"}\n\n{"name": "Andes Ltda"}\n'

    done = bus_with(repository)(
        CommandImportPartners(content=content, format=ImportFormat.JSON_LINES), ResponseImport
    )

    assert done.created == 2


def test_a_semicolon_csv_with_decimal_commas_reads_as_the_spreadsheet_wrote_it():
    repository = MemoryRepository()
    content = "name;credit\nACME SRL;1.234,50\n".encode("cp1252")

    bus_with(repository)(
        CommandImportPartners(
            content=content, format=ImportFormat.CSV, decimal=",", encoding="cp1252"
        )
    )

    assert stored(repository, Partner)[0].credit == Decimal("1234.50")


def test_an_excel_sheet_reads_its_numbers_dates_and_texts_as_the_fields_take_them():
    repository = MemoryRepository().add(BOLIVIA, PERU)

    done = bus_with(repository)(
        CommandImportPartners(
            content=WORKBOOK.read_bytes(),
            format=ImportFormat.EXCEL,
            sheet="Partners",
            columns={"Notas": None},
        ),
        ResponseImport,
    )

    assert done.created == 2
    acme, andes = stored(repository, Partner)
    assert (acme.vat, acme.since, acme.credit, acme.country_id) == (
        "1020304050",
        date(2019, 3, 1),
        Decimal("1500.75"),
        BOLIVIA.id,
    )
    assert (andes.vat, andes.since) == ("0012", date(2024, 12, 31))


def test_a_workbook_without_the_sheet_named_says_which_sheets_it_has():
    with pytest.raises(ddd_exceptions.InvalidValue, match="it has Notes, Partners"):
        bus_with(MemoryRepository())(
            CommandImportPartners(
                content=WORKBOOK.read_bytes(), format=ImportFormat.EXCEL, sheet="Clientes"
            )
        )


def test_an_old_mac_workbook_counts_its_dates_from_1904():
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>name</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>since</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>ACME SRL</t></is></c>'
        '<c r="B2" s="1"><v>0</v></c></row>'
        "</sheetData></worksheet>"
    )
    parts = {
        "xl/workbook.xml": (
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<workbookPr date1904="1"/><sheets><sheet name="Old" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
            "</Relationships>"
        ),
        "xl/styles.xml": (
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>'
        ),
        "xl/worksheets/sheet1.xml": sheet,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    repository = MemoryRepository()

    bus_with(repository)(
        CommandImportPartners(content=buffer.getvalue(), format=ImportFormat.EXCEL)
    )

    assert stored(repository, Partner)[0].since == date(1904, 1, 1)


def test_a_dry_run_judges_the_whole_file_and_writes_nothing():
    repository = MemoryRepository().add(BOLIVIA)
    content = csv("""
@key,name,country_id
p-1,ACME SRL,BO
""")

    shown = bus_with(repository)(
        CommandImportPartners(content=content, format=ImportFormat.CSV, dry_run=True), Preview
    )

    assert shown.text == "Would create 1, update 0 and leave 0 unchanged"
    assert shown.data["created"] == 1
    assert stored(repository, Partner) == []
    assert list(repository.fetch_all(ExternalKey)) == []


def test_a_dry_run_refuses_what_the_import_would_refuse():
    content = csv("""
name,country_id
ACME SRL,XX
""")

    with pytest.raises(ddd_exceptions.ImportRefused):
        bus_with(MemoryRepository())(
            CommandImportPartners(content=content, format=ImportFormat.CSV, dry_run=True)
        )


def test_mode_refuses_creating_what_exists_or_updating_what_does_not():
    repository = MemoryRepository().add(BOLIVIA)
    bus = bus_with(repository)
    content = csv("""
@key,name
p-1,ACME SRL
""")
    bus(CommandImportPartners(content=content, format=ImportFormat.CSV))

    with pytest.raises(
        ddd_exceptions.ImportRefused, match="a Partner with @key 'p-1' exists already"
    ):
        bus(
            CommandImportPartners(
                content=content, format=ImportFormat.CSV, mode=ImportMode.CREATE
            )
        )
    with pytest.raises(ddd_exceptions.ImportRefused, match="no Partner has @key 'p-9'"):
        bus(
            CommandImportPartners(
                content=content.replace(b"p-1", b"p-9"),
                format=ImportFormat.CSV,
                mode=ImportMode.UPDATE,
            )
        )


def test_a_key_repeated_in_the_file_is_refused_naming_the_first_record_holding_it():
    content = csv("""
@key,name
p-1,ACME SRL
p-1,ACME again
""")

    with pytest.raises(
        ddd_exceptions.ImportRefused, match="repeated: the record at 2 has it too"
    ):
        bus_with(MemoryRepository())(
            CommandImportPartners(content=content, format=ImportFormat.CSV)
        )


def test_each_reference_is_looked_up_once_per_import_whatever_the_number_of_batches():
    repository = MemoryRepository().add(BOLIVIA)
    asked: list[str] = []

    bus = UseFramework("imports-counted", log_after_execution=False)
    bus.add_dependency("repository", repository)

    @bus.feature(CommandImportPartners)
    class CountedImports(EntityImports[Partner]):
        def identities(self, dto, kind, by, keys):
            asked.append(f"{kind.__name__}.{by}")
            return super().identities(dto, kind, by, keys)

    rows = "\n".join(f"Partner {number},BO" for number in range(10))
    bus(
        CommandImportPartners(
            content=f"name,country_id\n{rows}".encode(), format=ImportFormat.CSV, batch_size=3
        )
    )

    assert asked.count("Country.code") == 1


def test_a_step_is_overridden_to_add_a_rule_of_the_import_alone():
    repository = MemoryRepository()

    bus = UseFramework("imports-ruled", log_after_execution=False)
    bus.add_dependency("repository", repository)

    @bus.feature(CommandImportPartners)
    class UpperImports(EntityImports[Partner]):
        def build(self, dto, batch):
            super().build(dto, batch)
            for one in batch:
                if one.record is not None:
                    one.record.name = one.record.name.upper()

    bus(CommandImportPartners(content=b"name\nacme srl\n", format=ImportFormat.CSV))

    assert stored(repository, Partner)[0].name == "ACME SRL"
