"""What a use case declares it measures — checked where it is written (PRD_03 §4.2, §4.3).

    @metrics.counts(by=of(CommandIssueInvoice).currency)        ->  declaring(...) on the class
    retries = metrics.counter(by=of(CommandIssueInvoice).channel)   ->  a DeclaredInstrument

Context: nothing is named by a string. A decorated metric is named after its bounded context,
its use case and the field it reads (`billing.issue_invoice.total`) — or `runs` for `counts`;
one declared on the class after its attribute (`billing.issue_invoice.retries`). Values and
labels are field references checked where they are written — a field that does not exist, a
label that is not bounded, a value that is not a number, a path into another DTO than the use
case's, all refused at import.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sincpro_framework.observability.domain import FAULT, OUTCOME
from sincpro_framework.observability.metrics.domain.bus_observability import BusObservability
from sincpro_framework.observability.metrics.domain.declarations import MetricDeclaration
from sincpro_framework.observability.metrics.domain.instruments import (
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.naming import metric_name
from sincpro_framework.observability.metrics.domain.paths import FieldPath
from sincpro_framework.observability.metrics.infrastructure.registry import (
    declarations_of,
    declare,
)
from sincpro_framework.observability.metrics.services.labels import (
    bounded_label,
    read_labels,
)
from sincpro_framework.observability.metrics.services.outcomes import (
    ERROR_TYPE,
    faulted,
    outcome_of,
)
from sincpro_framework.observability.metrics.services.recording import active_recorder

type Labels = Any | tuple[Any, ...]
"""One field reference, or several — `by=(of(Command).currency, of(Command).channel)`."""


def label_paths(by: Labels) -> tuple[FieldPath, ...]:
    """The `by=` of a declaration as field paths — a value handed instead is refused, a label
    that is not bounded (an Enum, a Literal, a bool) warned about once.

        label_paths(None)                                ->  ()
        label_paths(of(Command).currency)                ->  (of(Command).currency,)
        label_paths((of(Command).currency, of(Command).channel))  ->  both, in that order
    """
    references = by if isinstance(by, tuple) else (() if by is None else (by,))
    return tuple(bounded_label(FieldPath.required(one, "a label")) for one in references)


def declaring(declaration: MetricDeclaration):  # type: ignore[no-untyped-def]
    """The class decorator `counts`/`sums`/`measures` answer: it refuses, at import, a
    declaration whose labels or value start anywhere but the decorated use case's own Command,
    Response or declared context type, and registers `declaration` on it, answering the class
    unchanged."""

    def declared[T: type](cls: T) -> T:
        FieldPath.refuse_foreign(
            cls,
            (
                *declaration.labels,
                *(() if declaration.value is None else (declaration.value,)),
            ),
        )
        declare(cls, declaration)
        return cls

    return declared


def declared_on(cls: type) -> bool:
    """Whether `cls` — or what it inherits from — declared a metric with a decorator."""
    return isinstance(cls, type) and bool(declarations_of(cls))


class BoundInstrument:
    """An instrument of one use case on one bounded context — what `self.<attribute>` is."""

    def __init__(
        self,
        instrument: Instrument,
        labels: tuple[FieldPath, ...],
        owner: type,
        who: BusObservability | None = None,
    ):
        """Store the instrument, the label paths, the use case they belong to and the bus
        (`who`) that judges whether a failure timed by `time` is a fault."""
        self.instrument = instrument
        self._labels = labels
        self._owner = owner
        self._who = who

    def _read(self, sources: tuple[Any, ...]) -> dict[str, str]:
        """The label values — each path read off the source of its root's type (a context
        path off the execution context), `none` when none was handed."""
        return read_labels(self._labels, sources, self._owner)

    def add(self, value: float, *sources: Any) -> None:
        """A counter or up-down counter moves by `value`; labels read off `sources`."""
        active_recorder.emit(self.instrument, value, self._read(sources))

    def record(self, value: float, *sources: Any) -> None:
        """A histogram observes `value`; labels read off `sources`."""
        active_recorder.emit(self.instrument, value, self._read(sources))

    @contextmanager
    def time(self, *sources: Any) -> Iterator[None]:
        """The block's duration, in seconds, with its outcome — a failure as its kind.

            with self.pricing.time():
                ...      ->  0.042 observed with {"sincpro.outcome": "ok",
                             "sincpro.fault": "false", "error.type": ""}
            raising SiatDown (a ServiceUnavailableError)
                         ->  {"sincpro.outcome": "unavailable", "sincpro.fault": "true",
                              "error.type": "SiatDown"}, and SiatDown still propagates

        Measured on success and on failure alike; the fault is the bus's judgement (`who`)."""
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
                **outcome_of(error, faulted(self._who, error)),
            }
            active_recorder.emit(self.instrument, time.perf_counter() - started, labels)


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
        """Store what the instrument will be; its name waits for `__set_name__` and its
        bounded context for the first `bound_to`. `timed` adds the outcome labels.

            DeclaredInstrument(InstrumentKind.COUNTER, of(CommandIssueInvoice).channel, "",
                               "", None)
                ->  labels (of(CommandIssueInvoice).channel,), owner None, attribute ""

        The labels are checked here, as written (`label_paths`): a value instead of a field
        reference is refused, an unbounded one warned about."""
        self.kind = kind
        self.labels = label_paths(by)
        self.unit = unit
        self.description = description
        self.buckets = buckets
        self.timed = timed
        self.owner: type | None = None
        self.attribute = ""
        self._bound: dict[str, BoundInstrument] = {}

    def __set_name__(self, owner: type, name: str) -> None:
        """Learn the use case and the attribute — the last segment of the metric's name."""
        self.owner, self.attribute = owner, name

    def bound_to(self, context: str, who: BusObservability | None = None) -> BoundInstrument:
        """The instrument as the bus `context` records it — built once per context and stored.

        Context: one use case class can run on two buses; each gets its own series
        (`billing.issue_invoice.retries`, `sales.issue_invoice.retries`).

            IssueInvoice.retries.bound_to("billing")
                ->  BoundInstrument("billing.issue_invoice.retries", label_keys=("channel",))
            IssueInvoice.pricing.bound_to("billing").instrument.label_keys
                ->  ("sincpro.outcome", "sincpro.fault", "error.type")      # a timer, no by=

        1. A context already bound answers what it bound.
        2. The instrument is named `{context}.{use_case}.{attribute}`; a `timer` also carries
           `sincpro.outcome`, `sincpro.fault` and `error.type` among its label keys.
        Final: the `BoundInstrument`, stored for `context`.
        """
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
            label_keys=(*keys, OUTCOME, FAULT, ERROR_TYPE) if self.timed else keys,
            buckets=self.buckets,
        )
        bound = BoundInstrument(instrument, self.labels, owner, who)
        self._bound[context] = bound
        return bound

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        """On the class, the declaration itself; on a use case instance, the instrument bound
        to the bus that instance runs on (its `_context_binder`; none outside a bus).

            IssueInvoice.retries          ->  DeclaredInstrument
            self.retries                  ->  BoundInstrument("billing.issue_invoice.retries")
        """
        if instance is None:
            return self
        binder = getattr(instance, "_context_binder", None)
        return self.bound_to(
            getattr(binder, "name", "") or "", getattr(binder, "observability", None)
        )
