"""Where a bounded context is hosted, and how a DTO travels: the two pieces the core owns.

The context map — the conf file's list, `SINCPRO_CONTEXT_MAP` winning per context — names each
context's address; the message writes a DTO's values as JSON — `bytes`, `Decimal`, `datetime`,
`UUID`, enum members included — and the receiver rebuilds it with its own class, tolerant of what
it does not know.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

import pytest
from pydantic import ValidationError

from sincpro_framework import DataTransferObject, sincpro_conf
from sincpro_framework.common.serialization import pack, unpack
from sincpro_framework.remote_execution import (
    HostedAt,
    HostedContext,
    InvalidAddress,
    Wire,
)
from sincpro_framework.remote_execution.infrastructure.configuration import (
    _configured,
    configured_host,
)
from sincpro_framework.settings.domain.config import DefaultFrameworkConfig


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


def test_an_address_is_read_into_its_wire_its_host_and_its_deadline():
    assert HostedAt.parse("grpc://10.0.0.5:50051?timeout=5") == HostedAt(
        Wire.GRPC, "10.0.0.5:50051", 5.0
    )
    assert HostedAt.parse(" https://catalog.example:443 ") == HostedAt(
        Wire.HTTPS, "catalog.example:443", 30.0
    )
    assert str(HostedAt(Wire.HTTP, "c:8000", 2)) == "http://c:8000?timeout=2"


def test_the_environment_names_each_context_and_its_address():
    entries = HostedContext.parse_all(
        "billing=grpc://10.0.0.5:50051?timeout=5, catalog=http://catalog:8000"
    )

    assert [(one.context, one.at) for one in entries] == [
        ("billing", HostedAt(Wire.GRPC, "10.0.0.5:50051", 5.0)),
        ("catalog", HostedAt(Wire.HTTP, "catalog:8000", 30.0)),
    ]
    assert HostedContext.parse_all(None) == [] and HostedContext.parse_all("  ") == []


def test_the_conf_file_is_validated_when_it_is_loaded_each_error_by_its_path():
    loaded = DefaultFrameworkConfig.model_validate(
        {"context_map": [{"context": "billing", "at": "grpc://billing-service:50051"}]}
    )
    assert loaded.context_map[0].at.wire is Wire.GRPC

    with pytest.raises(ValidationError) as refused:
        DefaultFrameworkConfig.model_validate(
            {
                "context_map": [
                    {"context": "billing", "at": "grcp://b:1"},
                    {"contxt": "catalog", "at": "grpc://c:2"},
                ]
            }
        )

    where = {".".join(str(one) for one in error["loc"]) for error in refused.value.errors()}
    assert where == {"context_map.0.at", "context_map.1.context", "context_map.1.contxt"}
    assert "'grcp' is not a wire" in str(refused.value)


def test_the_environment_wins_per_context_over_the_conf_file(monkeypatch):
    monkeypatch.delenv("SINCPRO_CONTEXT_MAP", raising=False)
    monkeypatch.setattr(
        sincpro_conf.settings,
        "context_map",
        [
            HostedContext(context="billing", at=HostedAt.parse("grpc://from-the-file:1")),
            HostedContext(context="catalog", at=HostedAt.parse("grpc://from-the-file:2")),
        ],
    )
    monkeypatch.setattr(
        sincpro_conf.settings, "context_map_override", "billing=http://from-env:3"
    )
    _configured.cache_clear()
    try:
        assert configured_host("billing") == HostedAt(Wire.HTTP, "from-env:3")
        assert configured_host("catalog") == HostedAt(Wire.GRPC, "from-the-file:2")
        assert configured_host("planning") is None
    finally:
        _configured.cache_clear()


def test_a_wrong_environment_names_the_variable(monkeypatch):
    monkeypatch.setenv("SINCPRO_CONTEXT_MAP", "billing=grpc://billing-svc")
    _configured.cache_clear()
    try:
        with pytest.raises(
            InvalidAddress, match="SINCPRO_CONTEXT_MAP: billing: 'grpc://billing-svc'"
        ):
            configured_host("billing")
    finally:
        _configured.cache_clear()


def test_an_unknown_option_is_a_warning_and_the_address_still_serves(caplog):
    with caplog.at_level("WARNING", logger="sincpro_framework"):
        hosted = HostedAt.parse("grpc://b:1?timeout=2&retries=3")

    assert hosted == HostedAt(Wire.GRPC, "b:1", 2.0)
    assert "retries is not an option" in caplog.text


@pytest.mark.parametrize(
    ("written", "said"),
    [
        ("billing-svc:50051", "names no wire — write grpc://billing-svc:50051"),
        ("ftp://b:1", "'ftp' is not a wire a context is reached by"),
        ("grpc://b", "'grpc://b': 'b' is not <host>:<port>"),
        ("grpc://:1", "':1' is not <host>:<port>"),
        ("grpc://b:1/billing", "a path (/billing) is not part of a context's address"),
        ("http://b:1?timeout=abc", "timeout=abc is not a number of seconds"),
        ("grpc://b:1?timeout=0", "a timeout of 0s never answers"),
    ],
)
def test_an_address_no_wire_reaches_is_refused_saying_what_to_write(written, said):
    with pytest.raises(InvalidAddress) as refused:
        HostedAt.parse(written)

    assert said in str(refused.value)


def test_an_environment_entry_that_names_no_context_is_refused():
    with pytest.raises(InvalidAddress, match="is not <context>=<address>"):
        HostedContext.parse_all("billing")
    with pytest.raises(InvalidAddress, match="is not <context>=<address>"):
        HostedContext.parse_all("=grpc://b:1")


def test_an_address_built_in_code_is_checked_as_one_read():
    with pytest.raises(InvalidAddress, match="'smtp' is not a wire"):
        HostedAt("smtp", "b:1")  # type: ignore[arg-type]
    with pytest.raises(InvalidAddress, match="is not <host>:<port>"):
        HostedAt(Wire.GRPC, "no-port")


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


def test_the_message_is_json_and_binary_never_reads_as_text():
    """Bytes travel tagged, so a PDF is never mistaken for a text field on the way back."""
    written = pack(CommandPing(note="%PDF", raw=b"%PDF\xff"))

    assert json.loads(written) == {"note": "%PDF", "raw": {"$bytes": "JVBERv8="}}
    assert unpack(written, CommandPing) == CommandPing(note="%PDF", raw=b"%PDF\xff")


def test_the_receiver_ignores_what_it_does_not_know_and_defaults_what_is_missing():
    class Older(DataTransferObject):
        sku: str
        units: int = 1

    assert unpack(b'{"sku": "A-1", "added_later": true}', Older) == Older(sku="A-1", units=1)
    with pytest.raises(ValidationError, match="sku"):
        unpack(b'{"units": 2}', Older)
