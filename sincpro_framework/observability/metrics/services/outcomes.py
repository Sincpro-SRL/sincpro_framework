"""How a run ended, as every metric of it says — its kind, whether it is a fault, the error's
class (PRD_03 §4.2). The use case's duration, a declared timer and the span read the same
judgement from here, so the three signals never disagree.
"""

from sincpro_framework.common.failures import is_fault, refined_failure_kind
from sincpro_framework.observability import correlation
from sincpro_framework.observability.domain import FAULT, OK, OUTCOME
from sincpro_framework.observability.metrics.domain.bus_observability import BusObservability

ERROR_TYPE = "error.type"


def faulted(who: BusObservability | None, error: BaseException | None) -> bool | None:
    """Whether the bus (`who`, its `Observability`; the one running when not given) judges
    `error` a fault — `None` when none can say, and the shared rule decides. A timer a use case
    declares reads the same bus its run is measured by, so both say the same.

        faulted(billing_observability, KeyError("x"))           ->  True
        faulted(billing_observability, InvoiceLocked())         ->  False   # the bus ignores it
        faulted(None, KeyError("x"))      ->  None, outside any bus's execution
        faulted(who, None)                ->  None: nothing failed

    1. No error: `None`.
    2. No `who`: the bus running the execution in play (`correlation.current`).
    3. One without a `faulted` judgement — or none at all: `None`.
    Final: its judgement as a bool; `None` when asking it raises.
    """
    if error is None:
        return None
    if who is None:
        who = correlation.current()  # type: ignore[assignment]
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
    and it is reported whatever the fault says, so a 404 and a 409 stay apart on a dashboard.
    `fault` is what the bus judged (`Observability.faulted`), the shared rule when it says
    nothing.
    """
    if error is None:
        return {OUTCOME: OK, FAULT: "false", ERROR_TYPE: ""}
    try:
        kind = (
            str(refined_failure_kind(error)) if isinstance(error, Exception) else "internal"
        )
    except Exception:
        kind = "internal"
    judged = is_fault(error) if fault is None else fault
    return {
        OUTCOME: kind,
        FAULT: "true" if judged else "false",
        ERROR_TYPE: type(error).__name__,
    }
