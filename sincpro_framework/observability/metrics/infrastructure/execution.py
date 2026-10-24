"""The metrics of one run of a use case — what every bus records, declared or not (PRD_03 §4.2).

    sincpro.use_case.duration   histogram, seconds — sincpro.context, sincpro.use_case,
                                sincpro.layer, sincpro.outcome, sincpro.fault, error.type

Context: one histogram gives the three signals a use case is watched by — its rate (the count),
its errors (the outcome) and its latency (the buckets) — for every use case of every bounded
context, without a line in any of them. A failure is recorded as its kind (`domain`, `invalid`,
`internal`, …, the classification every wire already shares), never as `ok`, even when an error
handler answered for it — and `sincpro.fault` says whether it is a problem of the service, what
an error-rate alert reads. Declared metrics are read off a successful run.
"""

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from sincpro_framework.common.naming import registered_name
from sincpro_framework.observability.domain import FAULT, OK, OUTCOME
from sincpro_framework.observability.metrics.domain.declarations import Declaration, Measure
from sincpro_framework.observability.metrics.domain.instruments import (
    DURATION_BUCKETS,
    SECONDS,
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.naming import metric_name
from sincpro_framework.observability.metrics.domain.paths import FieldPath, label_value
from sincpro_framework.observability.metrics.infrastructure.active import active
from sincpro_framework.observability.metrics.infrastructure.identity import (
    BusObservability,
    announce,
    identity_of,
)
from sincpro_framework.observability.metrics.infrastructure.registry import declarations_of
from sincpro_framework.sincpro_logger import logger

ERROR_TYPE = "error.type"
SERVICE_NAME = "service.name"
RUNS = "runs"
"""What `counts` is named after: `billing.issue_invoice.runs`, never the bare use case — a field
summed under the same use case (`total`) would collide with it on Prometheus."""

USE_CASE_DURATION = Instrument(
    name="sincpro.use_case.duration",
    kind=InstrumentKind.HISTOGRAM,
    unit=SECONDS,
    description="How long each use case ran, by bounded context and outcome",
    label_keys=(
        SERVICE_NAME,
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


def faulted(who: BusObservability | None, error: BaseException | None) -> bool | None:
    """Whether the bus (`who`, its `Observability`; the one running when not given) judges
    `error` a fault — `None` when none can say, and the shared rule decides. A timer a use case
    declares reads the same bus its run is measured by, so both say the same."""
    if error is None:
        return None
    if who is None:
        from sincpro_framework.observability.correlation import current

        who = current()  # type: ignore[assignment]
    check = getattr(who, "faulted", None)
    if check is None:
        return None
    try:
        return bool(check(error))
    except Exception:
        return None


def outcome_of(error: BaseException | None, fault: bool | None = None) -> dict[str, str]:
    """How a run ended — `ok` or the failure's kind — whether that is a fault, and the error's
    class. All bounded: kinds are an enum, faults a boolean, classes are the code's.

        in      None                                      →  ok, fault false
        in      InvoiceNotFound (a ClientError)           →  not_found, fault false
        in      SiatDown (a ServiceUnavailableError)      →  unavailable, fault true

    Context: the kind is the refined one every wire encodes — what the error's class declares
    (`failure_kind = NOT_FOUND`), the idempotency refusals, then the shared classification —
    and it is kept whatever the fault says, so a 404 and a 409 stay apart on a dashboard. `fault`
    is what the bus judged (`Observability.faulted`), the shared rule when it says nothing.
    """
    if error is None:
        return {OUTCOME: OK, FAULT: "false", ERROR_TYPE: ""}
    from sincpro_framework.common.failures import is_fault, refined_failure_kind

    try:
        kind = (
            str(refined_failure_kind(error)) if isinstance(error, Exception) else "internal"
        )
    except Exception:
        kind = "internal"
    faulted = is_fault(error) if fault is None else fault
    return {
        OUTCOME: kind,
        FAULT: "true" if faulted else "false",
        ERROR_TYPE: type(error).__name__,
    }


_declared: dict[tuple[str, type, int], Instrument] = {}


_warned: set[tuple[type, str]] = set()


def _declared_instrument(
    context: str, use_case: type, declaration: Declaration
) -> Instrument:
    key = (context, use_case, id(declaration))
    known = _declared.get(key)
    if known is not None:
        return known
    rest = (RUNS,) if declaration.value is None else (declaration.value.key,)
    kind = {
        Measure.COUNTS: InstrumentKind.COUNTER,
        Measure.SUMS: InstrumentKind.COUNTER,
        Measure.MEASURES: InstrumentKind.HISTOGRAM,
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


def read_labels(
    paths: tuple[FieldPath, ...], sources: tuple[Any, ...], owner: type
) -> dict[str, str]:
    """Each label read off the source its path starts at — `none` when no source is one."""
    labels: dict[str, str] = {}
    for path in paths:
        if path.reads_context:
            labels[path.key] = label_value(path.read_context())
            continue
        source = next((one for one in sources if isinstance(one, path.root)), None)
        if source is None:
            if (owner, path.key) not in _warned:
                _warned.add((owner, path.key))
                logger.warning(
                    f"metrics: {owner.__name__} was handed no {path.root.__name__} to read "
                    f"{path!r} from; labelled none"
                )
            labels[path.key] = "none"
            continue
        labels[path.key] = label_value(path.read(source))
    return labels


def _record_declared(context: str, use_case: type, dto: Any, response: Any) -> None:
    for declaration in declarations_of(use_case):
        instrument = _declared_instrument(context, use_case, declaration)
        labels = read_labels(declaration.labels, (dto, response), use_case)
        if declaration.value is None:
            active.emit(instrument, 1, labels)
            continue
        if declaration.value.reads_context:
            value = declaration.value.read_context()
        else:
            source = dto if isinstance(dto, declaration.value.root) else response
            if not isinstance(source, declaration.value.root):
                continue
            value = declaration.value.read(source)
        if value is not None:
            active.emit(instrument, float(value), labels)


class Execution:
    """A run in progress: when it started, how it ended."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.error: BaseException | None = None
        self.response: Any = None

    def failed(self, error: BaseException) -> None:
        self.error = error

    def answered(self, response: Any) -> None:
        self.response = response


def _announce(context: str, who: BusObservability | None) -> None:
    """Who runs this context, once per recorder — see `identity`."""
    identity = identity_of(who)
    recorder = active.get()
    if identity is None or recorder is None:
        return
    try:
        announce(recorder, identity, context)
    except Exception as error:
        logger.warning(f"metrics: {context} not announced — {error}")


def _finish(
    execution: Execution,
    context: str,
    dto: Any,
    handler: Any,
    layer: str,
    who: BusObservability | None,
) -> None:
    _announce(context, who)
    identity = identity_of(who)
    labels: Mapping[str, str] = {
        SERVICE_NAME: identity.service_name if identity is not None else "",
        "sincpro.context": context,
        "sincpro.use_case": registered_name(type(dto), context),
        "sincpro.layer": layer,
        **outcome_of(execution.error, faulted(who, execution.error)),
    }
    active.emit(USE_CASE_DURATION, time.perf_counter() - execution.started, labels)
    if execution.error is None:
        _record_declared(context, type(handler), dto, execution.response)


@contextmanager
def measured(
    context: str,
    dto: Any,
    handler: Any,
    layer: str,
    who: BusObservability | None = None,
) -> Iterator[Execution]:
    """`who` answers `.identity` — the bus's `Observability`, read only when something records.

    1. Nothing to record to: the run goes through untouched.
    2. Final: the run is timed; an exception crossing it is its failure; the metrics are
       recorded, shielded, whatever happened."""
    execution = Execution()
    if active.get() is None:
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
