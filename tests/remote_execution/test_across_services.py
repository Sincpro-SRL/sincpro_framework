"""A bounded context hosted by another service: the caller's `bus(dto, Response)` is answered there.

Every case runs over both transports. Service B hosts its `billing` context — with
`serve(..., Attach.THREAD)` over gRPC, or with the HTTP route mounted on an app of its own — and
service A is a `billing` bus built from the same code and `hosted_by` B. What A gets back — the
response, the error, the timeout — is what A's code would have seen locally.
"""

import asyncio
import time
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd.exceptions import DomainError
from sincpro_framework.remote_execution import (
    ContextTimeout,
    ContextUnavailable,
    HostedAt,
    Wire,
)
from sincpro_framework.remote_execution.adapters.transport import transport_for
from tests.remote_execution.hosting import TRANSPORTS, host_over


class InvoiceRefused(DomainError):
    """A refusal of the project's own, raised on B and caught on A by its class."""


class ProjectError(Exception):
    """A project's own exception that is not a `DomainError` — raised as itself too."""


class SignerDown(Exception):
    """An exception that needs more than a message to be built — raised as itself all the same."""

    def __init__(self, signer: str, attempts: int) -> None:
        super().__init__(f"{signer} is down after {attempts} attempts")


class CommandIssueInvoice(DataTransferObject):
    total: Decimal
    pdf: bytes


class ResponseIssueInvoice(DataTransferObject):
    number: str
    total: Decimal
    pdf_size: int
    issued_by: int | None
    served_at: str


class CommandEcho(DataTransferObject):
    data: bytes


class ResponseEcho(DataTransferObject):
    data: bytes


class CommandFail(DataTransferObject):
    how: str


class CommandSlow(DataTransferObject):
    seconds: float


@dataclass
class ServedHere:
    """Where each execution ran."""

    places: list[str]


def _billing(place: str, served: ServedHere) -> UseFramework:
    """The `billing` context, as both services build it from the same code."""
    billing = UseFramework("across-billing", log_after_execution=False)
    billing.add_dependency("served", served)
    billing.add_dependency("place", place)

    @billing.feature(CommandIssueInvoice)
    class IssueInvoice(Feature):
        served: ServedHere
        place: str

        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            self.served.places.append(self.place)
            return ResponseIssueInvoice(
                number="F-101",
                total=dto.total,
                pdf_size=len(dto.pdf),
                issued_by=self.context.get("user_id"),
                served_at=self.place,
            )

    @billing.feature(CommandEcho)
    class Echo(Feature):
        def execute(self, dto: CommandEcho) -> ResponseEcho:
            return ResponseEcho(data=dto.data)

    @billing.feature(CommandFail)
    class Fail(Feature):
        def execute(self, dto: CommandFail) -> None:
            if dto.how == "domain":
                raise InvoiceRefused("the customer is over its credit limit")
            if dto.how == "project":
                raise ProjectError("the invoice series is closed")
            if dto.how == "builtin":
                raise ValueError("the total is negative")
            raise SignerDown("hsm-1", 3)

    @billing.feature(CommandSlow)
    class Slow(Feature):
        def execute(self, dto: CommandSlow) -> None:
            time.sleep(dto.seconds)

    return billing


@pytest.fixture(params=TRANSPORTS)
def service_b(request: pytest.FixtureRequest) -> Iterator[tuple[str, ServedHere]]:
    served = ServedHere([])
    yield from (
        (address, served) for address in host_over(request.param, [_billing("B", served)])
    )


def _service_a(address: str, timeout: float = 5) -> tuple[UseFramework, ServedHere]:
    served = ServedHere([])
    billing = _billing("A", served)
    billing.hosted_by(f"{address}?timeout={timeout}")
    return billing, served


def test_the_call_is_answered_by_the_service_hosting_the_context(service_b):
    address, served_on_b = service_b
    billing, served_on_a = _service_a(address)
    pdf = b"%PDF-1.7\x00\xff binary"

    answer = billing(
        CommandIssueInvoice(total=Decimal("100.10"), pdf=pdf), ResponseIssueInvoice
    )

    assert answer.served_at == "B" and answer.number == "F-101"
    assert answer.total == Decimal("100.10") and answer.pdf_size == len(pdf)
    assert served_on_b.places == ["B"] and served_on_a.places == []


def test_a_payload_far_past_any_per_message_limit_travels_both_ways_byte_exact(service_b):
    """96 MiB — gRPC refuses a single message past 4 MB by default: this one travels in chunks."""
    address, _served = service_b
    billing, _ = _service_a(address, timeout=60)
    data = bytes(range(256)) * (96 * 1024 * 1024 // 256)

    answer = billing(CommandEcho(data=data), ResponseEcho)

    assert len(answer.data) == 96 * 1024 * 1024 and answer.data == data


def test_without_a_response_type_it_answers_what_the_handler_declares(service_b):
    address, _served = service_b
    billing, _ = _service_a(address)

    answer = billing(CommandIssueInvoice(total=Decimal("1"), pdf=b""))

    assert isinstance(answer, ResponseIssueInvoice) and answer.served_at == "B"


def test_the_request_context_crosses_with_its_types(service_b):
    address, _served = service_b
    billing, _ = _service_a(address)

    with billing.context({"user_id": 42}):
        answer = billing(
            CommandIssueInvoice(total=Decimal("1"), pdf=b""), ResponseIssueInvoice
        )

    assert answer.issued_by == 42


def test_a_domain_error_raised_there_is_raised_here_as_itself(service_b):
    address, _served = service_b
    billing, _ = _service_a(address)

    with pytest.raises(InvoiceRefused, match="over its credit limit"):
        billing(CommandFail(how="domain"))


def test_any_error_whose_class_is_imported_here_is_raised_as_itself(service_b):
    """Open by default: not only a `DomainError` — a project's own exception, a builtin one."""
    address, _served = service_b
    billing, _ = _service_a(address)

    with pytest.raises(ProjectError, match="series is closed"):
        billing(CommandFail(how="project"))
    with pytest.raises(ValueError, match="total is negative"):
        billing(CommandFail(how="builtin"))


def test_an_error_whose_constructor_takes_more_than_a_message_arrives_as_itself(service_b):
    address, _served = service_b
    billing, _ = _service_a(address)

    with pytest.raises(SignerDown, match="hsm-1 is down after 3 attempts"):
        billing(CommandFail(how="other"))


def test_a_call_past_its_deadline_is_context_timeout(service_b):
    address, _served = service_b
    billing, _ = _service_a(address, timeout=0.2)

    with pytest.raises(ContextTimeout, match="within 0.2s"):
        billing(CommandSlow(seconds=1))


@pytest.mark.parametrize("scheme", TRANSPORTS)
def test_nobody_answering_is_context_unavailable(scheme):
    billing, _ = _service_a(f"{scheme}://127.0.0.1:9", timeout=1)

    with pytest.raises(ContextUnavailable):
        billing(CommandIssueInvoice(total=Decimal("1"), pdf=b""))


def test_a_service_that_does_not_host_the_context_is_context_unavailable(service_b):
    address, _served = service_b
    catalog = UseFramework("across-catalog", log_after_execution=False)

    @catalog.feature(CommandSlow)
    class Slow(Feature):
        def execute(self, dto: CommandSlow) -> None: ...

    catalog.hosted_by(address)

    with pytest.raises(ContextUnavailable, match="does not host 'across-catalog'"):
        catalog(CommandSlow(seconds=0))


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_the_service_hosting_a_context_runs_it_here_even_when_its_map_says_elsewhere(
    transport,
):
    """Two services often share an environment: the one hosting `billing` may read
    `billing=…itself` too — it answers, rather than forwarding the call it is answering."""
    served = ServedHere([])
    billing_on_b = _billing("B", served)
    for address in host_over(transport, [billing_on_b]):
        billing_on_b.hosted_by(address)
        billing_on_a, _ = _service_a(address)

        answer = billing_on_a(
            CommandIssueInvoice(total=Decimal("1"), pdf=b""), ResponseIssueInvoice
        )

        assert answer.served_at == "B" and served.places == ["B"]


def test_the_async_facade_of_a_context_hosted_elsewhere_is_answered_there_too(service_b):
    address, served_on_b = service_b
    billing, served_on_a = _service_a(address)

    async def issue() -> ResponseIssueInvoice | None:
        return await billing.get_async_bus().execute(
            CommandIssueInvoice(total=Decimal("3"), pdf=b"\x00"), ResponseIssueInvoice
        )

    answer = asyncio.run(issue())

    assert answer is not None and answer.served_at == "B"
    assert served_on_b.places == ["B"] and served_on_a.places == []


def test_a_context_nobody_mapped_elsewhere_runs_here():
    served = ServedHere([])
    billing = _billing("A", served)

    answer = billing(CommandIssueInvoice(total=Decimal("1"), pdf=b""), ResponseIssueInvoice)

    assert billing.hosted_at is None and answer.served_at == "A"


@pytest.mark.parametrize("scheme", TRANSPORTS)
def test_the_transport_to_one_address_is_shared(scheme):
    hosted_at = HostedAt(Wire(scheme), "127.0.0.1:9", 1.0)

    assert transport_for(hosted_at) is transport_for(hosted_at)
