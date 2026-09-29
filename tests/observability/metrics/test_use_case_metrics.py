"""Metrics of the use cases (PRD_03 §4): the ones every bus records by itself, the ones a use
case declares with a decorator, and the ones it records by hand inside `execute`.

Each rule is an incident it prevents: a dashboard that counts a failure as a success, a label
that makes one series per customer and takes Prometheus down, a metric named by a string that
drifts from the code, a broken exporter that fails the use case it was only measuring.
"""

from collections.abc import Iterator
from decimal import Decimal
from enum import StrEnum
from typing import Literal

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd.exceptions import ContractViolation, DomainError
from sincpro_framework.observability.metrics import (
    InMemoryRecorder,
    Recorder,
    declares_metrics,
    metrics,
    of,
)

USE_CASE_DURATION = "sincpro.use_case.duration"


class Currency(StrEnum):
    BOB = "BOB"
    USD = "USD"


class CommandIssueInvoice(DataTransferObject):
    customer_id: str
    currency: Currency
    total: Decimal
    channel: Literal["web", "pos"] = "web"


class ResponseIssueInvoice(DataTransferObject):
    number: str
    total: Decimal
    lines: int


class InvoiceRefused(DomainError):
    pass


@pytest.fixture
def recorder() -> Iterator[InMemoryRecorder]:
    recorded = InMemoryRecorder()
    with metrics.using(recorded):
        yield recorded


def _billing() -> UseFramework:
    bus = UseFramework("billing", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @metrics.counts(by=of(CommandIssueInvoice).currency)
    @metrics.sums(of(ResponseIssueInvoice).total, by=of(CommandIssueInvoice).currency)
    @metrics.measures(of(ResponseIssueInvoice).lines, buckets=(1, 5, 10))
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            if dto.total < 0:
                raise InvoiceRefused("an invoice cannot be negative")
            return ResponseIssueInvoice(number="F-1", total=dto.total, lines=3)

    return bus


def _issue(bus: UseFramework, total: str = "10", currency: Currency = Currency.BOB) -> None:
    bus(
        CommandIssueInvoice(customer_id="c-1", currency=currency, total=Decimal(total)),
        ResponseIssueInvoice,
    )


# --- recorded by every bus, declaring nothing -------------------------------------------------


def test_every_use_case_is_timed_with_its_context_and_outcome(recorder):
    """Rate, errors and latency of every use case of every bounded context — without one line
    in the use case."""
    bus = UseFramework("crm", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    class Plain(Feature):
        def execute(self, dto: CommandIssueInvoice) -> None:
            return None

    bus(CommandIssueInvoice(customer_id="c", currency=Currency.BOB, total=Decimal(1)))

    (series,) = recorder.observations(USE_CASE_DURATION)
    labels, values = series
    assert labels == {
        "sincpro.context": "crm",
        "sincpro.use_case": "CommandIssueInvoice",
        "sincpro.layer": "feature",
        "sincpro.outcome": "ok",
        "error.type": "",
    }
    assert len(values) == 1 and values[0] >= 0


def test_a_failure_is_timed_as_its_kind_never_as_ok(recorder):
    bus = _billing()

    with pytest.raises(InvoiceRefused):
        _issue(bus, total="-1")

    (series,) = recorder.observations(USE_CASE_DURATION)
    assert series[0]["sincpro.outcome"] == "domain"
    assert series[0]["error.type"] == "InvoiceRefused"


@pytest.mark.parametrize("answered_by", ["global", "feature"])
def test_an_error_answered_by_a_handler_is_still_a_failure(recorder, answered_by):
    """The caller got an answer, the use case still failed: a dashboard of successes that counts
    it hides the outage — whichever bus's handler answered."""
    bus = _billing()
    if answered_by == "global":
        bus.add_global_error_handler(lambda error: None)
    else:
        bus.add_feature_error_handler(lambda error: None)

    _issue(bus, total="-1")

    (series,) = recorder.observations(USE_CASE_DURATION)
    assert series[0]["sincpro.outcome"] == "domain"
    assert recorder.totals("billing.issue_invoice.runs") == {}


def test_an_application_service_and_each_feature_it_runs_are_timed_apart(recorder):
    bus = _billing()

    class CommandIssueTwice(DataTransferObject):
        pass

    @bus.app_service(CommandIssueTwice)
    class IssueTwice(ApplicationService):
        def execute(self, dto: CommandIssueTwice) -> None:
            for _ in range(2):
                self.feature_bus.execute(
                    CommandIssueInvoice(
                        customer_id="c", currency=Currency.USD, total=Decimal(5)
                    ),
                    ResponseIssueInvoice,
                )

    bus(CommandIssueTwice())

    timed = {
        (labels["sincpro.use_case"], labels["sincpro.layer"]): len(values)
        for labels, values in recorder.observations(USE_CASE_DURATION)
    }
    assert timed == {
        ("CommandIssueTwice", "application_service"): 1,
        ("CommandIssueInvoice", "feature"): 2,
    }


# --- declared: decorate, and it counts, sums or measures an attribute -------------------------


def test_counts_each_success_by_the_label_it_names(recorder):
    bus = _billing()

    _issue(bus, currency=Currency.BOB)
    _issue(bus, currency=Currency.BOB)
    _issue(bus, currency=Currency.USD)
    with pytest.raises(InvoiceRefused):
        _issue(bus, total="-1")

    assert recorder.totals("billing.issue_invoice.runs") == {
        (("currency", "BOB"),): 2,
        (("currency", "USD"),): 1,
    }


def test_sums_an_attribute_of_the_answer_and_measures_another(recorder):
    bus = _billing()

    _issue(bus, total="10.5")
    _issue(bus, total="4.5")

    assert recorder.totals("billing.issue_invoice.total") == {(("currency", "BOB"),): 15.0}
    ((_, lines),) = recorder.observations("billing.issue_invoice.lines")
    assert lines == [3.0, 3.0]
    assert recorder.instrument("billing.issue_invoice.lines").buckets == (1, 5, 10)


def test_a_use_case_says_what_it_measures(recorder):
    bus = _billing()
    handler = bus.handler_of(CommandIssueInvoice)

    assert handler is not None and declares_metrics(handler)
    assert not declares_metrics(Feature)


# --- what a declaration refuses, at import time -----------------------------------------------


def test_an_attribute_the_dto_does_not_have_is_refused_where_it_is_written():
    """A metric named by a field reference cannot drift from the code: a renamed field fails
    at import, not as a silent empty series in a dashboard."""
    with pytest.raises(ContractViolation, match="CommandIssueInvoice has no field 'curency'"):
        of(CommandIssueInvoice).curency  # type: ignore[attr-defined]


def test_an_unbounded_label_is_refused():
    """One series per customer id is how a label takes the metrics backend down."""
    with pytest.raises(ContractViolation, match="customer_id is str"):
        metrics.counts(by=of(CommandIssueInvoice).customer_id)


def test_only_a_number_is_summed_or_measured():
    with pytest.raises(ContractViolation, match="currency is not a number"):
        metrics.sums(of(CommandIssueInvoice).currency)


def test_a_path_of_another_dto_than_the_use_cases_is_refused():
    class CommandOther(DataTransferObject):
        amount: int

    with pytest.raises(ContractViolation, match="CommandOther is neither"):

        @metrics.sums(of(CommandOther).amount)
        class IssueInvoice(Feature):
            def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice: ...


def test_a_label_is_a_field_reference_never_a_value():
    with pytest.raises(ContractViolation, match="of\\(Command\\)"):
        metrics.counts(by=Currency.BOB)  # type: ignore[arg-type]


# --- by hand, inside execute: named by the attribute, never by a string -----------------------


def test_instruments_declared_on_the_use_case_are_named_by_their_attribute(recorder):
    bus = UseFramework("billing", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    class IssueInvoice(Feature):
        rejected_lines = metrics.counter(by=of(CommandIssueInvoice).channel)
        pricing = metrics.timer()
        discount = metrics.histogram(unit="BOB", buckets=(0, 10, 100))
        pending = metrics.up_down()

        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            self.rejected_lines.add(2, dto)
            with self.pricing.time():
                self.discount.record(7)
            self.pending.add(1)
            self.pending.add(-1)
            return ResponseIssueInvoice(number="F-1", total=dto.total, lines=1)

    bus(
        CommandIssueInvoice(customer_id="c", currency=Currency.BOB, total=Decimal(1)),
        ResponseIssueInvoice,
    )

    assert recorder.totals("billing.issue_invoice.rejected_lines") == {
        (("channel", "web"),): 2
    }
    ((pricing_labels, pricing),) = recorder.observations("billing.issue_invoice.pricing")
    assert pricing_labels == {"sincpro.outcome": "ok", "error.type": ""} and len(pricing) == 1
    assert recorder.instrument("billing.issue_invoice.pricing").unit == "s"
    assert recorder.observations("billing.issue_invoice.discount") == [({}, [7.0])]
    assert recorder.totals("billing.issue_invoice.pending") == {(): 0}


def test_a_timed_block_that_raises_is_timed_as_its_failure(recorder):
    bus = UseFramework("billing", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    class IssueInvoice(Feature):
        pricing = metrics.timer()

        def execute(self, dto: CommandIssueInvoice) -> None:
            with self.pricing.time():
                raise InvoiceRefused("no price list")

    with pytest.raises(InvoiceRefused):
        bus(CommandIssueInvoice(customer_id="c", currency=Currency.BOB, total=Decimal(1)))

    ((labels, _),) = recorder.observations("billing.issue_invoice.pricing")
    assert labels == {"sincpro.outcome": "domain", "error.type": "InvoiceRefused"}


# --- measuring never breaks what it measures --------------------------------------------------


class Broken(Recorder):
    def add(self, instrument, value, labels) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("the exporter is down")

    def record(self, instrument, value, labels) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("the exporter is down")


def test_a_recorder_that_raises_never_fails_the_use_case():
    bus = _billing()

    with metrics.using(Broken()):
        _issue(bus)
        with pytest.raises(InvoiceRefused):
            _issue(bus, total="-1")


def test_a_recorder_that_raises_never_fails_an_instrument_recorded_by_hand():
    """Inside `execute` nothing else stands between the instrument and the use case."""
    bus = UseFramework("billing", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    class IssueInvoice(Feature):
        seen = metrics.counter()
        pricing = metrics.timer()

        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            self.seen.add(1)
            with self.pricing.time():
                pass
            return ResponseIssueInvoice(number="F-1", total=dto.total, lines=1)

    with metrics.using(Broken()):
        answer = bus(
            CommandIssueInvoice(customer_id="c", currency=Currency.BOB, total=Decimal(1)),
            ResponseIssueInvoice,
        )

    assert answer is not None and answer.number == "F-1"


def test_nothing_is_recorded_without_a_recorder():
    """The default without a backend configured: no-op, and the bus runs as without metrics."""
    bus = _billing()

    with metrics.using(None):
        _issue(bus)


def test_a_counter_never_goes_down(recorder):
    """A refund summed as a negative total would make a monotonic counter fall — Prometheus reads
    a fall as a restart and invents the difference as a rate spike."""

    class CommandRefund(DataTransferObject):
        amount: Decimal

    class ResponseRefund(DataTransferObject):
        amount: Decimal

    refunds = UseFramework("billing", log_after_execution=False)

    @refunds.feature(CommandRefund)
    @metrics.sums(of(ResponseRefund).amount)
    class Refund(Feature):
        def execute(self, dto: CommandRefund) -> ResponseRefund:
            return ResponseRefund(amount=dto.amount)

    refunds(CommandRefund(amount=Decimal(5)), ResponseRefund)
    refunds(CommandRefund(amount=Decimal(-3)), ResponseRefund)

    assert recorder.totals("billing.refund.amount") == {(): 5.0}
