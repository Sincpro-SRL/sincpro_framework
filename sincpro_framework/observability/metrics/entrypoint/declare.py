"""`metrics`: what a use case measures, declared on it (PRD_03 §4.2, §4.3).

    @billing.feature(CommandIssueInvoice)
    @metrics.counts(by=of(CommandIssueInvoice).currency)                      # each success
    @metrics.sums(of(ResponseIssueInvoice).total, by=of(CommandIssueInvoice).currency)
    @metrics.measures(of(ResponseIssueInvoice).lines, buckets=(1, 5, 10, 50))
    class IssueInvoice(Feature):
        retries = metrics.counter(by=of(CommandIssueInvoice).channel)       # by hand, in execute
        pricing = metrics.timer()

        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            with self.pricing.time():
                ...
            self.retries.add(1, dto)

Context: nothing is named by a string. A decorated metric is named after its bounded context,
its use case and the field it reads (`billing.issue_invoice.total`) — or `runs` for `counts`; one declared on the class
after its attribute (`billing.issue_invoice.retries`). Values and labels are field references
checked where they are written — a field that does not exist, a label that is not bounded, a
value that is not a number, a path into another DTO than the use case's, all refused at import.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sincpro_framework.observability.metrics.domain.declarations import Declaration, Measure
from sincpro_framework.observability.metrics.domain.instruments import (
    DURATION_BUCKETS,
    SECONDS,
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.naming import metric_name
from sincpro_framework.observability.metrics.domain.paths import (
    FieldPath,
    bounded_label,
    field_path,
    numeric_value,
    refuse_foreign_paths,
)
from sincpro_framework.observability.metrics.domain.recorder import Recorder
from sincpro_framework.observability.metrics.infrastructure.active import active
from sincpro_framework.observability.metrics.infrastructure.execution import (
    ERROR_TYPE,
    OUTCOME,
    expects,
    outcome_of,
    read_labels,
)
from sincpro_framework.observability.metrics.infrastructure.identity import BusObservability
from sincpro_framework.observability.metrics.infrastructure.registry import (
    declarations_of,
    declare,
)

type Labels = Any | tuple[Any, ...]
"""One field reference, or several — `by=(of(Command).currency, of(Command).channel)`."""


def _labels(by: Labels) -> tuple[FieldPath, ...]:
    references = by if isinstance(by, tuple) else (() if by is None else (by,))
    return tuple(bounded_label(field_path(one, "a label")) for one in references)


def _refuse_foreign_paths(cls: type, declaration: Declaration) -> None:
    refuse_foreign_paths(
        cls,
        (*declaration.labels, *(() if declaration.value is None else (declaration.value,))),
    )


def _declaring(declaration: Declaration):  # type: ignore[no-untyped-def]
    def declared[T: type](cls: T) -> T:
        _refuse_foreign_paths(cls, declaration)
        declare(cls, declaration)
        return cls

    return declared


def declares_metrics(cls: type) -> bool:
    """Whether `cls` — or what it inherits from — declared a metric with a decorator."""
    return isinstance(cls, type) and bool(declarations_of(cls))


# --- instruments declared on the use case, recorded by hand --------------------------------------


class BoundInstrument:
    """An instrument of one use case on one bounded context — what `self.<attribute>` is."""

    def __init__(
        self,
        instrument: Instrument,
        labels: tuple[FieldPath, ...],
        owner: type,
        expects: BusObservability | None = None,
    ):
        self.instrument = instrument
        self._labels = labels
        self._owner = owner
        self._expects = expects

    def _read(self, sources: tuple[Any, ...]) -> dict[str, str]:
        return read_labels(self._labels, sources, self._owner)

    def add(self, value: float, *sources: Any) -> None:
        """A counter or up-down counter moves by `value`; labels read off `sources`."""
        active.emit(self.instrument, value, self._read(sources))

    def record(self, value: float, *sources: Any) -> None:
        """A histogram observes `value`; labels read off `sources`."""
        active.emit(self.instrument, value, self._read(sources))

    @contextmanager
    def time(self, *sources: Any) -> Iterator[None]:
        """The block's duration, in seconds, with its outcome — a failure as its kind."""
        started = time.perf_counter()
        error: BaseException | None = None
        try:
            yield
        except BaseException as raised:
            error = raised
            raise
        finally:
            labels = {
                **self._read(sources),
                **outcome_of(error, expects(self._expects, error)),
            }
            active.emit(self.instrument, time.perf_counter() - started, labels)


class DeclaredInstrument:
    """An instrument declared as a class attribute; named by the attribute it is assigned to."""

    def __init__(
        self,
        kind: InstrumentKind,
        by: Labels,
        unit: str,
        description: str,
        buckets: tuple[float, ...] | None,
        timed: bool = False,
    ) -> None:
        self.kind = kind
        self.labels = _labels(by)
        self.unit = unit
        self.description = description
        self.buckets = buckets
        self.timed = timed
        self.owner: type | None = None
        self.attribute = ""
        self._bound: dict[str, BoundInstrument] = {}

    def __set_name__(self, owner: type, name: str) -> None:
        self.owner, self.attribute = owner, name

    def bound_to(self, context: str, who: BusObservability | None = None) -> BoundInstrument:
        known = self._bound.get(context)
        if known is not None:
            return known
        owner = self.owner or object
        keys = tuple(one.key for one in self.labels)
        instrument = Instrument(
            name=metric_name(context, owner, self.attribute),
            kind=self.kind,
            unit=self.unit,
            description=self.description,
            label_keys=(*keys, OUTCOME, ERROR_TYPE) if self.timed else keys,
            buckets=self.buckets,
        )
        bound = BoundInstrument(instrument, self.labels, owner, who)
        self._bound[context] = bound
        return bound

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        binder = getattr(instance, "_context_binder", None)
        return self.bound_to(
            getattr(binder, "name", "") or "", getattr(binder, "observability", None)
        )


class Metrics:
    """The process's metrics: what use cases declare, and which recorder takes it."""

    # declared — decorate, and it measures

    def counts(self, by: Labels = None, description: str = ""):  # type: ignore[no-untyped-def]
        """One more for each successful run, per label — `{context}.{use_case}.runs`."""
        return _declaring(
            Declaration(measure=Measure.COUNTS, labels=_labels(by), description=description)
        )

    def sums(  # type: ignore[no-untyped-def]
        self, value: Any, by: Labels = None, unit: str = "", description: str = ""
    ):
        """A field of the Command or the Response, added up on each successful run —
        `{context}.{use_case}.{field}`."""
        return _declaring(
            Declaration(
                measure=Measure.SUMS,
                value=numeric_value(field_path(value, "what is summed")),
                labels=_labels(by),
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
        return _declaring(
            Declaration(
                measure=Measure.MEASURES,
                value=numeric_value(field_path(value, "what is measured")),
                labels=_labels(by),
                unit=unit,
                buckets=buckets,
                description=description,
            )
        )

    # by hand — a class attribute of the use case, named by it

    def counter(self, by: Labels = None, unit: str = "", description: str = "") -> Any:
        return DeclaredInstrument(InstrumentKind.COUNTER, by, unit, description, None)

    def up_down(self, by: Labels = None, unit: str = "", description: str = "") -> Any:
        return DeclaredInstrument(InstrumentKind.UP_DOWN, by, unit, description, None)

    def histogram(
        self,
        by: Labels = None,
        unit: str = "",
        buckets: tuple[float, ...] | None = None,
        description: str = "",
    ) -> Any:
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

    # which recorder takes it

    @property
    def recorder(self) -> Recorder | None:
        return active.get()

    def use(self, recorder: Recorder | None) -> None:
        """This process records into `recorder` from now on — `None` records nothing."""
        active.set(recorder)

    @contextmanager
    def using(self, recorder: Recorder | None) -> Iterator[Recorder | None]:
        """`recorder` for the block, the previous one back after it — for tests."""
        previous = active.set(recorder)
        try:
            yield recorder
        finally:
            active.restore(previous)


metrics = Metrics()
