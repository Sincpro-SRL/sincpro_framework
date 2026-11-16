"""An import on a real engine: references found by their natural and external keys, a
many-to-many named by name, children gathered from the rows below, the external keys stored in
their own table — and one unit of work, so a failure in a later batch undoes the earlier ones.

    imp_country         code UNIQUE
    imp_partner         country_id → imp_country, parent_id → imp_partner, labels through imp_partner_label
    imp_product         code UNIQUE
    imp_invoice         number UNIQUE, partner_id → imp_partner
    imp_invoice_line    invoice_id → imp_invoice, product_id → imp_product
    imp_external_keys   the names another system gives the records

And the way back: the records exported as the CSV or Excel sheet the import reads, imported
again unchanged.
"""

import csv
import io
from dataclasses import dataclass, field

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework import UseFramework, exceptions
from sincpro_framework.data_layer.orm import TableRelation, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import (
    entity_table,
    external_key_table,
)
from sincpro_framework.ddd import (
    Entity,
    EntityCollection,
    EntityID,
    EntityImports,
    Export,
    ExportFormat,
    ExternalKey,
    Import,
    ImportFormat,
    ResponseImport,
    Streamed,
)
from sincpro_framework.ddd import exceptions as ddd_exceptions

from .engines import fresh


@dataclass
class Country(Entity):
    code: str

    @classmethod
    def DEFAULT_GET_ID(cls) -> str:
        return "code"


@dataclass
class Label(Entity):
    name: str

    @classmethod
    def DEFAULT_GET_ID(cls) -> str:
        return "name"


@dataclass
class Partner(Entity):
    name: str
    country_id: "EntityID[Country] | None" = None
    parent_id: "EntityID[Partner] | None" = None
    labels: list[Label] = field(default_factory=list)


@dataclass
class Product(Entity):
    code: str

    @classmethod
    def DEFAULT_GET_ID(cls) -> str:
        return "code"


@dataclass
class InvoiceLine(Entity):
    invoice_id: str | None = None
    product_id: "EntityID[Product] | None" = None
    quantity: int = 0


@dataclass
class Invoice(Entity):
    number: str
    partner_id: "EntityID[Partner] | None" = None
    lines: EntityCollection[InvoiceLine] = field(default_factory=EntityCollection)


@dataclass
class SalesExternalKey(ExternalKey):
    pass


sales = registry(metadata=MetaData())
partner_label = Table(
    "imp_partner_label",
    sales.metadata,
    Column("partner_id", Text, ForeignKey("imp_partner.id"), primary_key=True),
    Column("label_id", Text, ForeignKey("imp_label.id"), primary_key=True),
)
map_aggregates(
    sales,
    {
        Country: entity_table(
            "imp_country", sales.metadata, Column("code", Text, nullable=False, unique=True)
        ),
        Label: entity_table(
            "imp_label", sales.metadata, Column("name", Text, nullable=False, unique=True)
        ),
        Partner: entity_table(
            "imp_partner",
            sales.metadata,
            Column("name", Text, nullable=False),
            Column("country_id", Text, ForeignKey("imp_country.id")),
            Column("parent_id", Text, ForeignKey("imp_partner.id")),
        ),
        Product: entity_table(
            "imp_product", sales.metadata, Column("code", Text, nullable=False, unique=True)
        ),
        Invoice: entity_table(
            "imp_invoice",
            sales.metadata,
            Column("number", Text, nullable=False, unique=True),
            Column("partner_id", Text, ForeignKey("imp_partner.id")),
        ),
        InvoiceLine: entity_table(
            "imp_invoice_line",
            sales.metadata,
            Column("invoice_id", Text, ForeignKey("imp_invoice.id"), nullable=False),
            Column("product_id", Text, ForeignKey("imp_product.id")),
            Column("quantity", Integer, nullable=False),
        ),
        SalesExternalKey: external_key_table("imp_external_keys", sales.metadata),
    },
    relations={
        Partner: {
            "labels": TableRelation.many_to_many(
                Label, through=partner_label, this_key="partner_id", related_key="label_id"
            )
        }
    },
)


class CommandImportPartners(Import[Partner, ResponseImport]):
    origin: str = "odoo"


class CommandImportInvoices(Import[Invoice, ResponseImport]):
    origin: str = "odoo"


class QueryExportPartners(Export[Partner, Streamed]):
    pass


class QueryExportInvoices(Export[Invoice, Streamed]):
    pass


@pytest.fixture
def store(engine_url: str) -> Repository:
    repository = Repository(fresh(engine_url, sales.metadata, enforce_foreign_keys=True))
    with repository.context() as unit:
        unit.save([Country(code="BO"), Country(code="PE")])
        unit.save([Label(name="vip"), Label(name="retail")])
        unit.save([Product(code="SKU-7"), Product(code="SKU-9")])
    return repository


@pytest.fixture
def bus(store: Repository) -> UseFramework:
    framework = UseFramework("imports-sql", log_after_execution=False)
    framework.add_dependency("repository", store)

    @framework.feature(
        [
            CommandImportPartners,
            CommandImportInvoices,
            QueryExportPartners,
            QueryExportInvoices,
        ]
    )
    class SalesImports(EntityImports[Partner]):
        external_keys = SalesExternalKey

    return framework


PARTNERS = b"""@key,name,country_id,parent_id/@key,labels
res_partner_1,ACME SRL,BO,,"vip,retail"
res_partner_2,ACME Santa Cruz,BO,res_partner_1,retail
res_partner_3,Andes Ltda,PE,,
"""


def test_references_by_natural_and_external_key_and_a_many_to_many_by_name(bus, store):
    done = bus(
        CommandImportPartners(content=PARTNERS, format=ImportFormat.CSV), ResponseImport
    )

    assert done.created == 3
    with store.context() as unit:
        by_name = {one.name: one for one in unit.fetch_all(Partner)}
        bolivia = unit.get_by(Country, code="BO")
        acme, santa_cruz = by_name["ACME SRL"], by_name["ACME Santa Cruz"]
        assert (acme.country_id, santa_cruz.parent_id) == (bolivia.id, acme.id)
        assert sorted(label.name for label in acme.labels) == ["retail", "vip"]
        keys = {one.key: one.entity_id for one in unit.fetch_all(SalesExternalKey)}
    assert keys["res_partner_1"] == acme.id and len(keys) == 3


def test_importing_the_same_file_again_changes_nothing(bus, store):
    bus(CommandImportPartners(content=PARTNERS, format=ImportFormat.CSV))

    again = bus(
        CommandImportPartners(content=PARTNERS, format=ImportFormat.CSV), ResponseImport
    )

    assert (again.created, again.updated, again.unchanged) == (0, 0, 3)
    assert len(store.fetch_all(Partner)) == 3
    assert len(store.fetch_all(SalesExternalKey)) == 3


def test_an_invoice_and_its_lines_from_the_rows_below_it(bus, store):
    bus(CommandImportPartners(content=PARTNERS, format=ImportFormat.CSV))
    content = b"""number,partner_id/@key,lines/product_id,lines/quantity
F-1,res_partner_1,SKU-7,2
,,SKU-9,1
F-2,res_partner_3,SKU-9,4
"""

    done = bus(
        CommandImportInvoices(content=content, format=ImportFormat.CSV), ResponseImport
    )

    assert done.created == 2
    with store.context() as unit:
        first = unit.get_by(Invoice, number="F-1")
        assert sorted(line.quantity for line in first.lines) == [1, 2]


def test_a_failure_in_a_later_batch_undoes_the_batches_written_before_it(bus, store):
    content = PARTNERS + b"res_partner_4,Sur SA,XX,,\n"

    with pytest.raises(ddd_exceptions.ImportRefused, match="no Country has code 'XX'"):
        bus(CommandImportPartners(content=content, format=ImportFormat.CSV, batch_size=1))

    assert len(store.fetch_all(Partner)) == 0
    assert len(store.fetch_all(SalesExternalKey)) == 0


def test_external_keys_a_repository_does_not_store_are_refused_saying_what_to_map(store):
    framework = UseFramework("imports-sql-unmapped", log_after_execution=False)
    framework.add_dependency("repository", store)

    @framework.feature(CommandImportPartners)
    class PartnerImports(EntityImports[Partner]):
        pass

    with pytest.raises(exceptions.ProgrammingError, match="external_key_table"):
        framework(CommandImportPartners(content=PARTNERS, format=ImportFormat.CSV))
    assert len(store.fetch_all(Partner)) == 0


@pytest.mark.parametrize("format", [ExportFormat.CSV, ExportFormat.EXCEL])
def test_an_export_imported_again_changes_nothing(bus, store, format):
    bus(CommandImportPartners(content=PARTNERS, format=ImportFormat.CSV))
    bus(
        CommandImportInvoices(
            content=b"number,partner_id/@key,lines/product_id,lines/quantity\n"
            b"F-1,res_partner_1,SKU-7,2\n,,SKU-9,1\n",
            format=ImportFormat.CSV,
        )
    )

    again = [
        bus(
            command(
                content=b"".join(bus(query(format=format), Streamed)),
                format=ImportFormat(format.value),
            ),
            ResponseImport,
        )
        for query, command in (
            (QueryExportPartners, CommandImportPartners),
            (QueryExportInvoices, CommandImportInvoices),
        )
    ]

    assert [(one.created, one.updated, one.unchanged) for one in again] == [
        (0, 0, 3),
        (0, 0, 1),
    ]
    assert len(store.fetch_all(Partner)) == 3


def test_a_many_to_many_is_written_as_its_keys_and_a_reference_by_its_natural_key(bus, store):
    bus(CommandImportPartners(content=PARTNERS, format=ImportFormat.CSV))

    content = b"".join(bus(QueryExportPartners(format=ExportFormat.CSV), Streamed))
    found = {
        row["name"]: row for row in csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    }

    assert list(found["ACME SRL"]) == ["id", "name", "country_id", "parent_id", "labels"]
    assert found["ACME SRL"]["country_id"] == "BO"
    assert sorted(found["ACME SRL"]["labels"].split(",")) == ["retail", "vip"]
    assert found["ACME Santa Cruz"]["parent_id"] == found["ACME SRL"]["id"]
    assert found["Andes Ltda"]["labels"] == ""
