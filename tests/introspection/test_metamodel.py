"""The metamodel of a bus — every model its operations reach, as its published contract, with the
operations that belong to each, under one version — and the file a build writes from it.

A model that changes at runtime — an extension that overlays how a field is shown — is compiled
again with another fingerprint, said in the log, and the bus's metamodel answers another version.
"""

import json
from dataclasses import dataclass

from structlog.testing import capture_logs

from sincpro_framework import DataTransferObject, DryRunnable, Feature, Preview, UseFramework
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity, EntityReads, Get, ResponseRecord
from sincpro_framework.ddd.entity import extensions
from sincpro_framework.ddd.metamodel import contract_of, forget_models, model_of
from sincpro_framework.introspection import entity_tags, export_metamodel, metamodel_of


@dataclass
class Partner(Entity):
    name: str = ""


@dataclass
class Invoice(Entity):
    number: str = ""
    partner: Partner | None = None


class ResponseInvoice(ResponseRecord):
    invoice: Invoice


class QueryGetInvoice(Get[Invoice, ResponseInvoice]):
    pass


class CommandVoidInvoice(DryRunnable):
    invoice_id: str


class ResponseVoided(DataTransferObject):
    voided: bool


def billing() -> UseFramework:
    bus = UseFramework("billing-metamodel", log_after_execution=False)
    bus.add_dependency("repository", MemoryRepository())

    @bus.feature(QueryGetInvoice)
    class InvoiceReads(EntityReads[Invoice]):
        pass

    @bus.feature(CommandVoidInvoice)
    @entity_tags(Invoice)
    class VoidInvoice(Feature):
        def preview(self, dto: CommandVoidInvoice) -> Preview:
            return Preview(text=f"void {dto.invoice_id}")

        def execute(self, dto: CommandVoidInvoice) -> ResponseVoided:
            return ResponseVoided(voided=True)

    return bus


def test_every_model_the_operations_reach_is_its_contract():
    metamodel = metamodel_of(billing())
    invoice = contract_of(Invoice)

    assert sorted(metamodel["models"]) == ["Invoice", "Partner"]
    assert metamodel["models"]["Partner"] == invoice.pop("$defs")["Partner"]
    assert metamodel["models"]["Invoice"] == invoice


def test_each_operation_says_the_entities_it_belongs_to_and_whether_it_runs_dry():
    operations = metamodel_of(billing())["operations"]

    assert operations["QueryGetInvoice"]["entities"] == ["Invoice"]
    assert operations["QueryGetInvoice"]["dry_run"] is False
    assert operations["CommandVoidInvoice"]["entities"] == ["Invoice"]
    assert operations["CommandVoidInvoice"]["dry_run"] is True


def test_the_same_code_writes_the_same_bytes_and_the_version_is_their_fingerprint(tmp_path):
    first, second = tmp_path / "first.json", tmp_path / "second.json"

    version = export_metamodel(billing(), first)
    export_metamodel(billing(), second)

    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text()) == metamodel_of(billing())
    assert version == metamodel_of(billing())["version"]


def test_an_extension_that_overlays_a_field_changes_the_model_and_says_so():
    bus = billing()
    before = metamodel_of(bus)["version"]
    model_before = model_of(Invoice).fingerprint
    try:
        extensions.overlay(Invoice, "number", {"label": {"default": "Folio"}})
        with capture_logs() as logs:
            forget_models(Invoice)
            after = metamodel_of(bus)

        assert after["version"] != before
        assert model_of(Invoice).fingerprint != model_before
        assert after["models"]["Invoice"]["properties"]["number"]["title"] == "Folio"
        assert [one["event"] for one in logs if "changed" in one["event"]] == [
            f"metamodel: Invoice changed, version {model_before[:12]} -> "
            f"{model_of(Invoice).fingerprint[:12]}"
        ]
    finally:
        extensions.OVERLAYS.pop(Invoice, None)
        forget_models(Invoice)
