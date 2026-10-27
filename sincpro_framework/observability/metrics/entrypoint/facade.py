"""`metrics`: what a use case measures, declared on it, and which recorder takes it (PRD_03 §4.2,
§4.3, §4.5).

    @billing.feature(CommandIssueInvoice)
    @metrics.counts(by=field_path(CommandIssueInvoice).currency)  # each success
    @metrics.sums(field_path(ResponseIssueInvoice).total,
                  by=field_path(CommandIssueInvoice).currency)
    @metrics.measures(field_path(ResponseIssueInvoice).lines, buckets=(1, 5, 10, 50))
    class IssueInvoice(Feature):
        # by hand, in execute
        retries = metrics.counter(by=field_path(CommandIssueInvoice).channel)
        pricing = metrics.timer()

        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            with self.pricing.time():
                ...
            self.retries.add(1, dto)

Context: the facade only gathers the operations — declaring (`services.declaring`), measuring a
run and judging how it ended (`services.measuring`, `services.outcomes`), and the process's
recorder (`services.recording`); what is checked, and how, lives there.
"""

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from sincpro_framework.observability.field_paths import FieldPath
from sincpro_framework.observability.metrics.domain.bus_observability import BusObservability
from sincpro_framework.observability.metrics.domain.declarations import (
    MetricDeclaration,
    MetricDecorator,
)
from sincpro_framework.observability.metrics.domain.instruments import (
    DURATION_BUCKETS,
    SECONDS,
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.recorder import Recorder
from sincpro_framework.observability.metrics.services.declaring import (
    DeclaredInstrument,
    Labels,
    declared_on,
    declaring,
    label_paths,
)
from sincpro_framework.observability.metrics.services.measuring import (
    MeasuredExecution,
    measured,
    replayed,
)
from sincpro_framework.observability.metrics.services.outcomes import outcome_of
from sincpro_framework.observability.metrics.services.recording import active_recorder


class Metrics:
    """The process's metrics: what use cases declare, and which recorder takes it."""

    # declared — decorate, and it measures

    def counts(self, by: Labels = None, description: str = ""):  # type: ignore[no-untyped-def]
        """One more for each successful run, per label — `{context}.{use_case}.runs`."""
        return declaring(
            MetricDeclaration(
                measure=MetricDecorator.COUNTS,
                labels=label_paths(by),
                description=description,
            )
        )

    def sums(  # type: ignore[no-untyped-def]
        self, value: Any, by: Labels = None, unit: str = "", description: str = ""
    ):
        """A field of the Command or the Response, added up on each successful run —
        `{context}.{use_case}.{field}`."""
        return declaring(
            MetricDeclaration(
                measure=MetricDecorator.SUMS,
                value=FieldPath.required(value, "what is summed").numeric(),
                labels=label_paths(by),
                unit=unit,
                description=description,
            )
        )

    def measures(  # type: ignore[no-untyped-def]
        self,
        value: Any,
        by: Labels = None,
        unit: str = "",
        buckets: tuple[float, ...] | None = None,
        description: str = "",
    ):
        """A field of the Command or the Response as a distribution —
        `{context}.{use_case}.{field}`."""
        return declaring(
            MetricDeclaration(
                measure=MetricDecorator.MEASURES,
                value=FieldPath.required(value, "what is measured").numeric(),
                labels=label_paths(by),
                unit=unit,
                buckets=buckets,
                description=description,
            )
        )

    def declared_on(self, cls: type) -> bool:
        """Whether `cls` — or what it inherits from — declared a metric with a decorator.

        metrics.declared_on(IssueInvoice)   ->  True     # @metrics.counts(...) on it
        metrics.declared_on(Feature)        ->  False
        """
        return declared_on(cls)

    # by hand — a class attribute of the use case, named by it

    def counter(self, by: Labels = None, unit: str = "", description: str = "") -> Any:
        """A counter recorded by hand — `self.<attribute>.add(1, dto)`; only goes up.

        retries = metrics.counter(by=field_path(CommandIssueInvoice).channel)
        """
        return DeclaredInstrument(InstrumentKind.COUNTER, by, unit, description, None)

    def up_down(self, by: Labels = None, unit: str = "", description: str = "") -> Any:
        """An up-down counter recorded by hand — `self.<attribute>.add(-1, dto)` goes down."""
        return DeclaredInstrument(InstrumentKind.UP_DOWN, by, unit, description, None)

    def histogram(
        self,
        by: Labels = None,
        unit: str = "",
        buckets: tuple[float, ...] | None = None,
        description: str = "",
    ) -> Any:
        """A histogram recorded by hand — `self.<attribute>.record(value, dto)`; `buckets`
        `None` leaves them to the recorder."""
        return DeclaredInstrument(InstrumentKind.HISTOGRAM, by, unit, description, buckets)

    def timer(
        self,
        by: Labels = None,
        buckets: tuple[float, ...] | None = None,
        description: str = "",
    ) -> Any:
        """A histogram in seconds, recorded by `with self.<attribute>.time(...)`, with the
        block's outcome."""
        return DeclaredInstrument(
            InstrumentKind.HISTOGRAM,
            by,
            SECONDS,
            description,
            buckets or DURATION_BUCKETS,
            timed=True,
        )

    # what a bus's observability asks of every run

    def measured(
        self,
        context: str,
        dto: Any,
        handler: Any,
        layer: str,
        who: BusObservability | None = None,
    ) -> AbstractContextManager[MeasuredExecution]:
        """One run measured: its duration by outcome, then what its use case declares —
        `answered(response)` and `failed(error)` on what it yields.

            with metrics.measured("billing", command, IssueInvoice(), "feature", who) as run:
                run.answered(response)
        """
        return measured(context, dto, handler, layer, who)

    def outcome_of(
        self, error: BaseException | None, fault: bool | None = None
    ) -> dict[str, str]:
        """How a run ended, as every metric of it says — `sincpro.outcome`, `sincpro.fault`
        and the error's class.

            metrics.outcome_of(None)               ->  {"sincpro.outcome": "ok", ...}
            metrics.outcome_of(SiatDown(), True)   ->  {"sincpro.outcome": "unavailable", ...}
        """
        return outcome_of(error, fault)

    # which recorder takes it

    @property
    def recorder(self) -> Recorder | None:
        """The recorder this process records into — `None` when metrics are off."""
        return active_recorder.get()

    def replayed(self) -> None:
        """The run in progress was answered with a stored answer, without running — what the
        idempotency says: it is timed as `replayed`, and counts, sums and measures nothing."""
        replayed()

    def use(self, recorder: Recorder | None) -> None:
        """This process records into `recorder` from now on — `None` records nothing."""
        active_recorder.set(recorder)

    @contextmanager
    def using(self, recorder: Recorder | None) -> Iterator[Recorder | None]:
        """`recorder` for the block, the previous one back after it — for tests."""
        previous = active_recorder.set(recorder)
        try:
            yield recorder
        finally:
            active_recorder.restore(previous)

    def emit(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        """One measurement of an instrument described outside a use case — the cache's and the
        idempotency's outcomes, a queue's deliveries — through the same shield as every other:
        the execution's correlation labels added, a counter never going down, a recorder that
        fails logged once and never reaching the caller. Nothing when metrics are off.

            metrics.emit(QUEUE_DELIVERIES, 1, {"messaging.destination.name": "invoices"})
                ->  recorder.add(QUEUE_DELIVERIES + the correlation keys, 1, {...})
        """
        active_recorder.emit(instrument, value, labels)


metrics = Metrics()
