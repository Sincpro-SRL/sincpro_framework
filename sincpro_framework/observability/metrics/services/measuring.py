"""The metrics of one run of a use case — what every bus records, declared or not (PRD_03 §4.2).

    sincpro.use_case.duration   histogram, seconds — sincpro.context, sincpro.use_case,
                                sincpro.layer, sincpro.outcome, sincpro.fault, error.type
    sincpro.context.info        1 per bounded context — sincpro.context, sincpro.artifact,
                                sincpro.version, sincpro.tenant

Context: one histogram gives the three signals a use case is watched by — its rate (the count),
its errors (the outcome) and its latency (the buckets) — for every use case of every bounded
context, without a line in any of them. A failure is recorded as its kind (`domain`, `invalid`,
`internal`, …, the classification every wire already shares), never as `ok`, even when an error
handler answered for it — and `sincpro.fault` says whether it is a problem of the service, what
an error-rate alert reads. Declared metrics are read off a successful run.

Who a metric comes from has three levels (PRD_03 §4.9): the service is the resource (a stable
`service.name`, its version and tenant beside it); the context is `sincpro.context` on every
series; what runs it travels once, on `sincpro.context.info`, as a Prometheus `*_build_info`
does — a version in the job would start every series again at each deploy and break `rate()`
across it. Join it when asked: `* on (sincpro_context) group_left (sincpro_version)`.
"""

import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from weakref import WeakKeyDictionary

from sincpro_framework.common.naming import registered_name
from sincpro_framework.observability import correlation
from sincpro_framework.observability.domain import FAULT, OUTCOME, ObservabilityIdentity
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
from sincpro_framework.observability.metrics.domain.naming import metric_name
from sincpro_framework.observability.metrics.domain.recorder import Recorder
from sincpro_framework.observability.metrics.infrastructure.registry import declarations_of
from sincpro_framework.observability.metrics.services.labels import read_labels
from sincpro_framework.observability.metrics.services.outcomes import (
    ERROR_TYPE,
    faulted,
    outcome_of,
)
from sincpro_framework.observability.metrics.services.recording import active_recorder
from sincpro_framework.sincpro_logger import logger

RUNS = "runs"
"""What `counts` is named after: `billing.issue_invoice.runs`, never the bare use case — a field
summed under the same use case (`total`) would collide with it on Prometheus."""

USE_CASE_DURATION = Instrument(
    name="sincpro.use_case.duration",
    kind=InstrumentKind.HISTOGRAM,
    unit=SECONDS,
    description="How long each use case ran, by bounded context and outcome",
    label_keys=(
        correlation.SERVICE_NAME,
        "sincpro.context",
        "sincpro.use_case",
        "sincpro.layer",
        OUTCOME,
        FAULT,
        ERROR_TYPE,
    ),
    buckets=DURATION_BUCKETS,
)
"""`service.name` is the library or service behind the context — one value per context, so it
adds no series. It is there because the job is the process's: inside Odoo the job is the host,
and this label is what finds the SDK, as `resource.service.name` does on its spans."""

CONTEXT_INFO = Instrument(
    name="sincpro.context.info",
    kind=InstrumentKind.UP_DOWN,
    description="1 for each bounded context of the process: the library and version that run it",
    label_keys=("sincpro.context", "sincpro.artifact", "sincpro.version", "sincpro.tenant"),
    correlated=False,
)

_announced: "WeakKeyDictionary[Recorder, set[tuple[str, ...]]]" = WeakKeyDictionary()
_announcing = threading.Lock()


def announce(recorder: Recorder, identity: ObservabilityIdentity, context: str) -> bool:
    """`sincpro.context.info` once per context and recorder — whichever recorder records, even
    one chosen after the bus was built. Answers whether it had to be announced now.

        announce(recorder, ObservabilityIdentity(artifact="billing-sdk", version="1.2.0"),
                 "billing")
            ->  True: sincpro.context.info{sincpro.context="billing",
                sincpro.artifact="billing-sdk", sincpro.version="1.2.0",
                sincpro.tenant="acme"} += 1
        announce(recorder, the same identity, "billing")   ->  False, nothing recorded

    Recorded straight on `recorder`, not through the shield: a recorder that raises reaches
    the caller, after the series was already marked as announced."""
    labels = {
        "sincpro.context": context,
        "sincpro.artifact": identity.service,
        "sincpro.version": identity.service_version,
        "sincpro.tenant": correlation.deployment_tenant(),
    }
    key = tuple(labels.values())
    with _announcing:
        seen = _announced.setdefault(recorder, set())
        if key in seen:
            return False
        seen.add(key)
    recorder.add(CONTEXT_INFO, 1, labels)
    return True


_declared: dict[tuple[str, type, int], Instrument] = {}


def _declared_instrument(
    context: str, use_case: type, declaration: MetricDeclaration
) -> Instrument:
    """The instrument a decorator declaration records into on the bus `context` — built the
    first time and stored per context, use case and declaration.

        @metrics.counts()         on billing  ->  counter    "billing.issue_invoice.runs"
        @metrics.sums(of(R).total)            ->  counter    "billing.issue_invoice.total"
        @metrics.measures(of(R).lines)        ->  histogram  "billing.issue_invoice.lines"

    Context: with no description, the first line of the use case's docstring stands in, so a
    series is never undocumented on the backend.
    """
    key = (context, use_case, id(declaration))
    known = _declared.get(key)
    if known is not None:
        return known
    rest = (RUNS,) if declaration.value is None else (declaration.value.key,)
    kind = {
        MetricDecorator.COUNTS: InstrumentKind.COUNTER,
        MetricDecorator.SUMS: InstrumentKind.COUNTER,
        MetricDecorator.MEASURES: InstrumentKind.HISTOGRAM,
    }[declaration.measure]
    instrument = Instrument(
        name=metric_name(context, use_case, *rest),
        kind=kind,
        unit=declaration.unit,
        description=declaration.description
        or (use_case.__doc__ or "").strip().split("\n")[0],
        label_keys=tuple(one.key for one in declaration.labels),
        buckets=declaration.buckets,
    )
    _declared[key] = instrument
    return instrument


def _record_declared(context: str, use_case: type, dto: Any, response: Any) -> None:
    """Record what `use_case` declared, read off a successful run's Command and Response.

    1. For each declaration: its instrument, and its labels read off `(dto, response)`.
    2. `counts`: one more.
    3. `sums` / `measures`: the value read off the context, else the Command or the Response
       — whichever its path starts at; a run that handed neither records nothing.
    Final: a `None` value is skipped, any other emitted as a float.
    """
    for declaration in declarations_of(use_case):
        instrument = _declared_instrument(context, use_case, declaration)
        labels = read_labels(declaration.labels, (dto, response), use_case)
        if declaration.value is None:
            active_recorder.emit(instrument, 1, labels)
            continue
        if declaration.value.reads_context:
            value = declaration.value.read(correlation.execution_context())
        else:
            source = dto if isinstance(dto, declaration.value.root) else response
            if not isinstance(source, declaration.value.root):
                continue
            value = declaration.value.read(source)
        if value is not None:
            active_recorder.emit(instrument, float(value), labels)


class MeasuredExecution:
    """A run in progress: when it started, how it ended."""

    def __init__(self) -> None:
        """Start the clock — no error, no response yet."""
        self.started = time.perf_counter()
        self.error: BaseException | None = None
        self.response: Any = None

    def failed(self, error: BaseException) -> None:
        """The run failed with `error` — it is measured as the failure's kind, even when an
        error handler answers for it afterwards."""
        self.error = error

    def answered(self, response: Any) -> None:
        """The run answered `response` — what declared metrics read their values off."""
        self.response = response


def _announce(context: str, who: BusObservability | None) -> None:
    """Who runs this context, once per recorder — see `announce`."""
    identity = getattr(who, "identity", None)
    recorder = active_recorder.get()
    if identity is None or recorder is None:
        return
    try:
        announce(recorder, identity, context)
    except Exception as error:
        logger.warning(f"metrics: {context} not announced — {error}")


def _finish(
    execution: MeasuredExecution,
    context: str,
    dto: Any,
    handler: Any,
    layer: str,
    who: BusObservability | None,
) -> None:
    """Record a finished run: its duration, then — on success only — what it declared.

    1. The context is announced to the recorder (once per recorder — `announce`).
    2. `sincpro.use_case.duration` with the service, context, use case, layer and the outcome
       as the bus judges it.
    Final: no error — the declared metrics, read off the Command and the response.
    """
    _announce(context, who)
    identity = getattr(who, "identity", None)
    labels: Mapping[str, str] = {
        correlation.SERVICE_NAME: identity.service_name if identity is not None else "",
        "sincpro.context": context,
        "sincpro.use_case": registered_name(type(dto), context),
        "sincpro.layer": layer,
        **outcome_of(execution.error, faulted(who, execution.error)),
    }
    active_recorder.emit(USE_CASE_DURATION, time.perf_counter() - execution.started, labels)
    if execution.error is None:
        _record_declared(context, type(handler), dto, execution.response)


@contextmanager
def measured(
    context: str,
    dto: Any,
    handler: Any,
    layer: str,
    who: BusObservability | None = None,
) -> Iterator[MeasuredExecution]:
    """`who` answers `.identity` — the bus's `Observability`, read only when something records.

    1. Nothing to record to: the run goes through untouched.
    2. Final: the run is timed; an exception crossing it is its failure; the metrics are
       recorded, shielded, whatever happened.

        with measured("billing", command, IssueInvoice(), "feature", who) as run:
            run.answered(response)
        ->  sincpro.use_case.duration {"sincpro.context": "billing", "sincpro.layer":
            "feature", "sincpro.outcome": "ok", ...}, then IssueInvoice's declared metrics

    A failure records the duration with its kind and no declared metric; a recorder that
    fails is logged as a warning and never reaches the run."""
    execution = MeasuredExecution()
    if active_recorder.get() is None:
        yield execution
        return
    try:
        yield execution
    except BaseException as error:
        execution.failed(error)
        raise
    finally:
        try:
            _finish(execution, context, dto, handler, layer, who)
        except Exception as error:
            logger.warning(f"metrics: {type(dto).__name__} not recorded — {error}")
