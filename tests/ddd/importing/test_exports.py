"""An export the import reads back: a set exported to CSV or Excel, edited in a spreadsheet and
imported again updates the same records — none added, none lost.

The store here is `MemoryRepository`; `tests/data_layer/orm/test_imports_sql.py` exports a
many-to-many on SQLite.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    Criteria,
    Entity,
    EntityCollection,
    EntityID,
    EntityImports,
    Export,
    ExportFormat,
    Import,
    ImportFormat,
    ResponseImport,
    Specification,
    Streamed,
    compute,
)
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd.importing import excel
from sincpro_framework.ddd.importing.excel import SheetWriter, sheet_rows
from sincpro_framework.ddd.money import Money


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
    since: date | None = None
    credit: Decimal = Decimal("0")


@dataclass
class Product(Entity):
    code: str

    @classmethod
    def DEFAULT_GET_ID(cls) -> str:
        return "code"


@dataclass
class InvoiceLine(Entity):
    product_id: "EntityID[Product] | None" = None
    quantity: int = 0


@dataclass
class Invoice(Entity):
    number: str
    partner_id: "EntityID[Partner] | None" = None
    lines: EntityCollection[InvoiceLine] = field(default_factory=EntityCollection)
    units: int = 0

    @classmethod
    def DEFAULT_GET_ID(cls) -> str:
        return "number"

    @compute(lambda invoice: invoice.units, depends=lambda invoice: [invoice.lines])
    def units_of(self) -> int:
        return sum(line.quantity for line in self.lines)


@dataclass
class Listing(Entity):
    title: str
    tags: list[str] = field(default_factory=list)
    price: Money | None = None
    barcode: int = 0
    published: datetime | None = None
    active: bool = True


class CommandImportPartners(Import[Partner, ResponseImport]):
    pass


class CommandImportInvoices(Import[Invoice, ResponseImport]):
    pass


class CommandImportListings(Import[Listing, ResponseImport]):
    pass


class QueryExportPartners(Export[Partner, Streamed]):
    pass


class QueryExportInvoices(Export[Invoice, Streamed]):
    pass


class QueryExportListings(Export[Listing, Streamed]):
    pass


BOLIVIA = Country(code="BO", name="Bolivia")
PERU = Country(code="PE", name="Perú")
SKU_7 = Product(code="SKU-7")
SKU_9 = Product(code="SKU-9")


def bus_with(repository: MemoryRepository) -> UseFramework:
    bus = UseFramework("exports", log_after_execution=False)
    bus.add_dependency("repository", repository)

    @bus.feature(
        [
            CommandImportPartners,
            CommandImportInvoices,
            CommandImportListings,
            QueryExportPartners,
            QueryExportInvoices,
            QueryExportListings,
        ]
    )
    class Files(EntityImports[Partner]):
        pass

    return bus


def partners(repository: MemoryRepository) -> list[Partner]:
    acme = Partner(
        name="ACME SRL",
        vat="1020304050",
        country_id=BOLIVIA.id,
        since=date(2019, 3, 1),
        credit=Decimal("1500.75"),
    )
    andes = Partner(name="Andes Ltda", country_id=PERU.id)
    repository.add(BOLIVIA, PERU, acme, andes)
    return [acme, andes]


def stored[T](repository: MemoryRepository, kind: type[T], identity: str) -> T:
    found = repository.get(kind, identity)
    assert found is not None
    return found


def exported(bus: UseFramework, query: Export) -> bytes:
    return b"".join(bus(query, Streamed))


def rows(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))


def test_a_csv_names_each_record_by_its_identity_and_each_reference_by_its_natural_key():
    repository = MemoryRepository()
    acme, andes = partners(repository)

    answer = bus_with(repository)(QueryExportPartners(format=ExportFormat.CSV), Streamed)
    content = b"".join(answer)

    assert answer.media_type == "text/csv"
    assert answer.filename == "partner.csv"
    assert content.startswith("﻿".encode())
    assert sorted(rows(content)) == sorted(
        [
            ["id", "name", "vat", "country_id", "since", "credit"],
            [str(acme.id), "ACME SRL", "1020304050", "BO", "2019-03-01", "1500.75"],
            [str(andes.id), "Andes Ltda", "", "PE", "", "0"],
        ]
    )


@pytest.mark.parametrize("format", [ExportFormat.CSV, ExportFormat.EXCEL])
def test_importing_an_export_again_changes_nothing(format):
    repository = MemoryRepository()
    partners(repository)
    bus = bus_with(repository)

    content = exported(bus, QueryExportPartners(format=format))
    done = bus(
        CommandImportPartners(content=content, format=ImportFormat(format.value)),
        ResponseImport,
    )

    assert (done.created, done.updated, done.unchanged) == (0, 0, 2)
    assert done.ignored == []


def test_an_export_edited_in_a_spreadsheet_updates_the_same_records():
    repository = MemoryRepository()
    acme, andes = partners(repository)
    bus = bus_with(repository)
    edited = [
        [cell.replace("ACME SRL", "ACME S.R.L.").replace("PE", "BO") for cell in row]
        for row in rows(exported(bus, QueryExportPartners(format=ExportFormat.CSV)))
    ]
    text = io.StringIO()
    csv.writer(text).writerows(edited)

    done = bus(
        CommandImportPartners(content=text.getvalue().encode(), format=ImportFormat.CSV),
        ResponseImport,
    )

    assert (done.created, done.updated, done.unchanged) == (0, 2, 0)
    assert len(repository.fetch_all(Partner)) == 2
    assert stored(repository, Partner, acme.id).name == "ACME S.R.L."
    assert stored(repository, Partner, andes.id).country_id == BOLIVIA.id


def invoices(repository: MemoryRepository) -> Invoice:
    acme = Partner(name="ACME SRL")
    first = Invoice(
        number="F-1",
        partner_id=acme.id,
        lines=EntityCollection(
            (
                InvoiceLine(product_id=SKU_7.id, quantity=2),
                InvoiceLine(product_id=SKU_9.id, quantity=1),
            )
        ),
        units=3,
    )
    repository.add(SKU_7, SKU_9, acme, first, Invoice(number="F-2"))
    return first


def test_children_are_written_in_the_rows_below_their_record_and_computed_fields_left_out():
    repository = MemoryRepository()
    first = invoices(repository)
    acme = repository.fetch_all(Partner)[0]
    lines = list(first.lines)

    written = rows(
        exported(bus_with(repository), QueryExportInvoices(format=ExportFormat.CSV))
    )

    assert written[0] == [
        "id",
        "number",
        "partner_id",
        "lines/id",
        "lines/product_id",
        "lines/quantity",
    ]
    assert [str(first.id), "F-1", str(acme.id), str(lines[0].id), "SKU-7", "2"] in written
    assert ["", "", "", str(lines[1].id), "SKU-9", "1"] in written


@pytest.mark.parametrize("format", [ExportFormat.CSV, ExportFormat.EXCEL])
def test_an_invoice_and_its_lines_come_back_unchanged(format):
    repository = MemoryRepository()
    first = invoices(repository)
    bus = bus_with(repository)

    content = exported(bus, QueryExportInvoices(format=format))
    done = bus(
        CommandImportInvoices(content=content, format=ImportFormat(format.value)),
        ResponseImport,
    )

    assert (done.created, done.updated, done.unchanged) == (0, 0, 2)
    assert [line.id for line in stored(repository, Invoice, first.id).lines] == [
        line.id for line in first.lines
    ]


def test_a_line_taken_out_of_the_file_is_taken_out_of_its_invoice():
    repository = MemoryRepository()
    first = invoices(repository)
    bus = bus_with(repository)
    kept = [
        row
        for row in rows(exported(bus, QueryExportInvoices(format=ExportFormat.CSV)))
        if row[0] or row[4] != "SKU-9"
    ]
    text = io.StringIO()
    csv.writer(text).writerows(kept)

    done = bus(
        CommandImportInvoices(content=text.getvalue().encode(), format=ImportFormat.CSV),
        ResponseImport,
    )

    assert (done.updated, done.unchanged) == (1, 1)
    invoice = stored(repository, Invoice, first.id)
    assert [line.quantity for line in invoice.lines] == [2]
    assert invoice.units == 2


def test_a_specification_picks_the_columns_and_the_identity_always_travels():
    repository = MemoryRepository()
    partners(repository)
    bus = bus_with(repository)
    names_only = Criteria(specification=Specification({"name": Criteria()}))

    content = exported(bus, QueryExportPartners(criteria=names_only, format=ExportFormat.CSV))
    done = bus(
        CommandImportPartners(content=content, format=ImportFormat.CSV), ResponseImport
    )

    assert rows(content)[0] == ["id", "name"]
    assert done.unchanged == 2


def test_an_excel_sheet_holds_each_value_as_the_cell_that_shows_it():
    repository = MemoryRepository()
    acme, _ = partners(repository)

    answer = bus_with(repository)(QueryExportPartners(format=ExportFormat.EXCEL), Streamed)
    title, found = sheet_rows(b"".join(answer))
    cells = {row[1]: row for _, row in found}

    assert answer.filename == "partner.xlsx"
    assert title == "Partner"
    assert cells["name"] == ["id", "name", "vat", "country_id", "since", "credit"]
    assert cells["ACME SRL"] == [
        str(acme.id),
        "ACME SRL",
        "1020304050",
        "BO",
        datetime(2019, 3, 1),
        1500.75,
    ]


@pytest.mark.parametrize("format", [ExportFormat.CSV, ExportFormat.EXCEL])
def test_a_value_one_cell_cannot_hold_travels_as_json_and_comes_back(format):
    repository = MemoryRepository()
    listing = Listing(
        title="Mate",
        tags=["yerba", "bombilla"],
        price=Money(amount=Decimal("10.50"), currency="BOB"),
        barcode=7_790_000_123_456_789,
        published=datetime(2026, 11, 16, 10, 30, tzinfo=timezone.utc),
        active=False,
    )
    repository.add(listing)
    bus = bus_with(repository)

    content = exported(bus, QueryExportListings(format=format))
    done = bus(
        CommandImportListings(content=content, format=ImportFormat(format.value)),
        ResponseImport,
    )

    assert (done.updated, done.unchanged, done.skipped) == (0, 1, [])
    assert stored(repository, Listing, listing.id) == listing


def test_a_day_excel_cannot_show_as_a_date_travels_as_its_text():
    repository = MemoryRepository()
    repository.add(Partner(name="Casa Fundada", since=date(1899, 5, 1)))
    bus = bus_with(repository)

    content = exported(bus, QueryExportPartners(format=ExportFormat.EXCEL))
    done = bus(
        CommandImportPartners(content=content, format=ImportFormat.EXCEL), ResponseImport
    )

    assert [row for _, row in sheet_rows(content)[1]][1][4] == "1899-05-01"
    assert done.unchanged == 1


def test_an_excel_sheet_refuses_more_rows_than_it_holds(monkeypatch):
    monkeypatch.setattr(excel, "SHEET_ROWS", 2)

    with pytest.raises(ddd_exceptions.InvalidValue, match="export these as CSV"):
        with SheetWriter(io.BytesIO(), "Partner") as sheet:
            sheet.write([["name"], ["ACME SRL"], ["Andes Ltda"]])
