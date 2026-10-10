"""A message is known by its identity on the remote bus: two contexts that each answer a
`CommandCreate` are hosted by one service, and each caller reaches its own (PRD_29, G8, G9).
"""

from collections.abc import Iterator

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from tests.remote_execution.hosting import TRANSPORTS, host_over


def _context(name: str, place: str) -> UseFramework:
    """A context as every service builds it from the same code, with its own `CommandCreate`."""

    class CommandCreate(DataTransferObject):
        title: str

    class ResponseCreate(DataTransferObject):
        created: str

    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(CommandCreate)
    class Create(Feature):
        def execute(self, dto: CommandCreate) -> ResponseCreate:
            return ResponseCreate(created=f"{name} {dto.title} at {place}")

    return bus


@pytest.fixture(params=TRANSPORTS)
def address(request: pytest.FixtureRequest) -> Iterator[str]:
    hosted = [_context("identity-sales", "B"), _context("identity-billing", "B")]
    yield from host_over(request.param, hosted)


def test_two_contexts_with_a_command_create_each_are_reached_on_one_service(address):
    sales, billing = _context("identity-sales", "A"), _context("identity-billing", "A")
    for caller in (sales, billing):
        caller.hosted_by(f"{address}?timeout=5")
    [(sold, SalesCreate)] = sales.dto_registry.items()
    [(billed, BillingCreate)] = billing.dto_registry.items()

    assert (sold, billed) == (
        "identity-sales.CommandCreate",
        "identity-billing.CommandCreate",
    )
    assert (
        getattr(sales(SalesCreate(title="quote")), "created") == "identity-sales quote at B"
    )
    assert (
        getattr(billing(BillingCreate(title="invoice")), "created")
        == "identity-billing invoice at B"
    )
