"""Where a bounded context is hosted, and how a DTO travels: the two pieces the core owns.

The context map — the conf file's list, `SINCPRO_CONTEXT_MAP` winning per context — names each
context's address; the payload packs a DTO's values as Python keeps them — `bytes`, `Decimal`,
`datetime`, `UUID`, enum members — and rebuilds it with its own class, refusing on receipt anything
outside its allow-list.
"""

import pickle
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from sincpro_framework import DataTransferObject, sincpro_conf
from sincpro_framework.remote_execution import (
    CannotTravel,
    HostedAt,
    configured_host,
    context_map_of,
    pack,
    parse_context_map,
    unpack,
)
from sincpro_framework.remote_execution.configuration import _configured


class Currency(StrEnum):
    BOB = "BOB"
    USD = "USD"


class Line(DataTransferObject):
    sku: str
    amount: Decimal


class CommandIssueInvoice(DataTransferObject):
    customer_id: UUID
    total: Decimal
    currency: Currency
    issued_on: date
    at: datetime
    within: timedelta
    pdf: bytes
    lines: list[Line]
    tags: set[str]


class Innocent:
    """A class of the project: it never travels, only values do."""


@dataclass
class CommandPing:
    note: str
    raw: bytes


def _invoice() -> CommandIssueInvoice:
    return CommandIssueInvoice(
        customer_id=UUID("018f0000-0000-7000-8000-000000000001"),
        total=Decimal("100.10"),
        currency=Currency.USD,
        issued_on=date(2026, 9, 28),
        at=datetime(2026, 9, 28, 8, 30, tzinfo=UTC),
        within=timedelta(days=30),
        pdf=b"%PDF-1.7\x00\xff\xfe binary",
        lines=[Line(sku="A-1", amount=Decimal("0.10"))],
        tags={"urgent"},
    )


# ---------------------------------------------------------------------------------------------
# Where a context is hosted
# ---------------------------------------------------------------------------------------------


def test_the_environment_names_each_context_and_its_address():
    hosted = parse_context_map(
        "billing=grpc://10.0.0.5:50051?timeout=5, catalog=http://catalog:8000"
    )

    assert hosted == {
        "billing": HostedAt("grpc", "10.0.0.5:50051", 5.0),
        "catalog": HostedAt("http", "catalog:8000", 30.0),
    }


def test_the_conf_file_names_them_as_a_list():
    hosted = context_map_of(
        [
            {"context": "billing", "at": "grpc://billing-service:50051?timeout=5"},
            {"context": "catalog", "at": "https://catalog.example:443"},
        ]
    )

    assert hosted["billing"] == HostedAt("grpc", "billing-service:50051", 5.0)
    assert hosted["catalog"].scheme == "https"


def test_the_environment_wins_per_context_over_the_conf_file(monkeypatch):
    monkeypatch.setattr(
        sincpro_conf.settings,
        "context_map",
        [
            {"context": "billing", "at": "grpc://from-the-file:1"},
            {"context": "catalog", "at": "grpc://from-the-file:2"},
        ],
    )
    monkeypatch.setattr(
        sincpro_conf.settings, "context_map_override", "billing=http://from-env:3"
    )
    _configured.cache_clear()
    try:
        assert configured_host("billing") == HostedAt("http", "from-env:3")
        assert configured_host("catalog") == HostedAt("grpc", "from-the-file:2")
        assert configured_host("planning") is None
    finally:
        _configured.cache_clear()


def test_nothing_configured_hosts_nothing_elsewhere():
    assert parse_context_map(None) == {}
    assert parse_context_map("  ") == {}
    assert context_map_of([]) == {}


def test_an_unknown_option_is_a_warning_and_the_address_still_serves():
    with capture_logs() as logs:
        hosted = parse_context_map("billing=grpc://b:1?timeout=2&retries=3")

    assert hosted["billing"] == HostedAt("grpc", "b:1", 2.0)
    assert any("retries" in line["event"] for line in logs if line["log_level"] == "warning")


def test_an_address_no_transport_can_reach_is_refused():
    with pytest.raises(ValueError, match="grpc://"):
        parse_context_map("billing=ftp://b:1")
    with pytest.raises(ValueError, match="billing=grpc://"):
        parse_context_map("billing")
    with pytest.raises(ValueError, match="context map entry"):
        context_map_of([{"context": "billing"}])


# ---------------------------------------------------------------------------------------------
# How a DTO travels
# ---------------------------------------------------------------------------------------------


def test_a_dto_travels_with_its_bytes_decimals_dates_uuids_and_enums_as_they_are():
    invoice = _invoice()

    rebuilt = unpack(pack(invoice), CommandIssueInvoice)

    assert rebuilt == invoice
    assert rebuilt.pdf == b"%PDF-1.7\x00\xff\xfe binary"
    assert rebuilt.currency is Currency.USD


def test_a_dataclass_dto_travels_too():
    assert unpack(pack(CommandPing(note="hi", raw=b"\x00\x01")), CommandPing) == CommandPing(
        note="hi", raw=b"\x00\x01"
    )


def test_the_receiver_validates_with_its_own_class():
    """The value is the class's, not the wire's: what the class refuses is refused here."""
    wrong = pack({"customer_id": "not a uuid"})

    with pytest.raises(ValidationError):
        unpack(wrong, CommandIssueInvoice)


def test_nothing_and_plain_values_travel():
    assert unpack(pack(None), None) is None
    assert unpack(pack([1, "a", b"b"]), list) == [1, "a", b"b"]


def test_a_class_outside_the_allow_list_is_refused_before_anything_is_built():
    """No code runs on receipt: a payload naming any other class is refused as it is read."""
    smuggled = pickle.dumps(Innocent)

    with pytest.raises(CannotTravel, match="Innocent"):
        unpack(smuggled, None)
    with pytest.raises(CannotTravel, match="system"):
        unpack(b"cos\nsystem\n(S'echo pwned'\ntR.", None)
