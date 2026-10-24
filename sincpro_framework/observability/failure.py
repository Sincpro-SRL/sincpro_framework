"""What failed in one execution, and where: the facts the log, the span and GlitchTip share.

Context: a command crosses several handlers — an ApplicationService running Features, a
Feature calling another context's bus. The failure is described once, by the handler that
raised it, and reported once, by the outermost bus. State lives in ``ContextVar``s, so
concurrent executions never see each other's failures.

    with execution() as outermost:                  # True: the first bus of this call
        with handling("CreateInvoice"):
            try:
                ...
            except Exception as error:
                remember(error, dto, "billing", handler, "feature")  ->  Failure(...)
                raise
    failure_of(error).summary   ->  "CreateInvoiceFeature failed: KeyError: 'x'"
    was_reported(error)         ->  True, once `annotate` put its note on the error
"""

import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Generator, Iterator

from sincpro_framework.common.naming import registered_name

FRAMEWORK_PACKAGE = "sincpro_framework"
NOTE_PREFIX = "[sincpro] "


@dataclass(frozen=True)
class CodeLocation:
    """One frame of a traceback — the module, the function, the file and the line.

    str(location)   ->  "billing.features.create_invoice:42 in CreateInvoice.execute"
    """

    module: str
    function: str
    file: str
    line: int

    def __str__(self) -> str:
        """`module:line in function` — short enough for a log field."""
        return f"{self.module}:{self.line} in {self.function}"


@dataclass(frozen=True)
class Failure:
    """One failure as the handler that raised it saw it: the error, the DTO, the bus and
    handler it failed in, the chain of handlers it crossed and where in the code it broke.

    Context: recorded once, by the innermost handler (`remember`), and read by every outer
    one through `failure_of` — so the log line, the span and GlitchTip name the same place.
    """

    error: BaseException
    dto: object
    bus: str
    handler: str
    layer: str
    chain: tuple[str, ...]
    error_at: CodeLocation | None
    raised_at: CodeLocation | None
    trace_ids: dict[str, str] = field(default_factory=dict)
    """`trace_id`/`span_id` of the span it failed in. The outermost bus logs the failure after
    that span has ended — without them the error line of a run with no parent span (a cron, a
    direct call) would carry no trace at all."""

    @property
    def summary(self) -> str:
        """The headline of its log line.

        "CreateInvoiceFeature failed: KeyError: 'x'"
        """
        return f"{self.handler} failed: {type(self.error).__name__}: {self.error}"

    @property
    def note(self) -> str:
        """The note `annotate` adds to the exception, so a traceback printed far from the bus
        still says which use case failed and where; its prefix is what `was_reported` reads.

            "[sincpro] ApplyPayment → CreateInvoice: CreateInvoiceFeature (billing) failed at
             billing.features:42 in CreateInvoiceFeature.execute executing CreateInvoice(...)"
        """
        where = f" at {self.error_at}" if self.error_at else ""
        path = f"{' → '.join(self.chain)}: " if len(self.chain) > 1 else ""
        who = f"{self.handler} ({self.bus})"
        return f"{NOTE_PREFIX}{path}{who} failed{where} executing {self.dto!r}"

    def fields(self) -> dict[str, Any]:
        """The structured fields of its log line and GlitchTip event.

        Context: besides its own keys, it carries `sincpro_context`, `sincpro_use_case` and
        `sincpro_layer` — the names the metric labels use — so a log line can be matched to
        its metric; `error_at`/`raised_at` only when known, and the trace ids it failed under.

            failure.fields()
                ->  {"dto": "CreateInvoice(...)", "failed_in": "billing",
                     "handler": "CreateInvoiceFeature", "layer": "feature",
                     "error_type": "KeyError", "chain": "ApplyPayment → CreateInvoice",
                     "sincpro_context": "billing",
                     "sincpro_use_case": "billing.CreateInvoice", "sincpro_layer": "feature",
                     "error_at": "billing.features:42 in CreateInvoiceFeature.execute",
                     "trace_id": "4bf9…", "span_id": "00f0…"}

        A new dict on every call — a caller may add to it.
        """
        fields: dict[str, Any] = {
            "dto": repr(self.dto),
            "failed_in": self.bus,
            "handler": self.handler,
            "layer": self.layer,
            "error_type": type(self.error).__name__,
            "chain": " → ".join(self.chain),
            # Where it failed, under the keys every signal shares (the metric labels' names).
            "sincpro_context": self.bus,
            "sincpro_use_case": registered_name(type(self.dto), self.bus),
            "sincpro_layer": self.layer,
        }
        if self.error_at:
            fields["error_at"] = str(self.error_at)
        if self.raised_at:
            fields["raised_at"] = str(self.raised_at)
        fields.update(self.trace_ids)
        return fields


def _belongs_to(module: str, package: str) -> bool:
    """Whether `module` is `package` or one of its submodules — never a lookalike prefix
    (`billing_x` does not belong to `billing`)."""
    return module == package or module.startswith(package + ".")


def locate(
    error: BaseException, package: str
) -> tuple[CodeLocation | None, CodeLocation | None]:
    """Where `error` failed in the consumer's code, and where it was raised when elsewhere.

    Context: the log call always sits in the bus, so the logger's own ``filename`` names
    the framework. The traceback names the consumer's code.

        locate(zeep_fault, "billing")
            ->  (CodeLocation("billing.features", "CreateInvoiceFeature.execute", …, 42),
                 CodeLocation("zeep.wsdl.bindings.soap", "SoapBinding.process_error", …))
        locate(a KeyError raised in billing's own code, "billing")
            ->  (CodeLocation("billing.features", …), None)

    1. ``error_at``: the innermost frame of ``package`` — the handler's own code — or, when
       the handler lives elsewhere, the innermost frame outside the framework.
    Final: ``raised_at``: the frame that raised, when it is not ``error_at`` (a SOAP or
       HTTP library).
    """
    locations = [
        CodeLocation(
            frame.f_globals.get("__name__", ""),
            frame.f_code.co_qualname,
            frame.f_code.co_filename,
            line,
        )
        for frame, line in traceback.walk_tb(error.__traceback__)
    ]
    own = [loc for loc in locations if _belongs_to(loc.module, package)]
    outside = [loc for loc in locations if not _belongs_to(loc.module, FRAMEWORK_PACKAGE)]
    candidates = own or outside
    error_at = candidates[-1] if candidates else None
    raised_at = locations[-1] if locations and locations[-1] != error_at else None
    return error_at, raised_at


# ---------------------------------------------------------------------------------------------
# The execution in progress
# ---------------------------------------------------------------------------------------------

_chain: ContextVar[tuple[str, ...]] = ContextVar("sincpro_execution_chain", default=())
_failures: ContextVar[list[Failure] | None] = ContextVar(
    "sincpro_execution_failures", default=None
)


@contextmanager
def execution() -> Generator[bool, None, None]:
    """Open the failures of an execution — or join the one already open.

    Context: yields whether this is the outermost execution — the only one that reports
    an error escaping it. Inner ones join it.

        with execution() as outermost:        # outermost  ->  True
            with execution() as inner:        # inner      ->  False, same failures
                ...

    The failures recorded inside are dropped when the outermost block closes."""
    if _failures.get() is not None:
        yield False
        return
    token = _failures.set([])
    try:
        yield True
    finally:
        _failures.reset(token)


@contextmanager
def handling(dto_name: str) -> Generator[None, None, None]:
    """Push `dto_name` on the chain of handlers for as long as the block runs.

    Context: a failure records the chain it was raised in, so an error deep inside an
    ApplicationService reads `ApplyPayment → CreateInvoice`, not just the last handler.
    """
    token = _chain.set((*_chain.get(), dto_name))
    try:
        yield
    finally:
        _chain.reset(token)


def _causes(error: BaseException) -> Iterator[BaseException]:
    """`error`, then what caused it — `__cause__`, else `__context__` — down the chain.

    Context: a cycle (an exception that is its own context, by a re-raise) is cut by
    remembering what was already yielded, so it never loops.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _notes(error: BaseException) -> list[str]:
    """The `__notes__` of an exception, empty when it has none."""
    return getattr(error, "__notes__", [])


def failure_of(error: BaseException) -> Failure | None:
    """The failure recorded for `error` or for any of its causes — `None` when none was.

    Context: an error handler that maps a SOAP fault to the SDK's own exception still
    leads back to the fault, through ``__cause__`` / ``__context__``."""
    for recorded in reversed(_failures.get() or []):
        if any(cause is recorded.error for cause in _causes(error)):
            return recorded
    return None


def remember(
    error: BaseException,
    dto: object,
    bus: str,
    handler: object,
    layer: str,
    trace_ids: dict[str, str] | None = None,
) -> Failure | None:
    """Record the failure of a use case's handler with `error`, located in the code.

    Context: ``None`` when an inner handler already recorded it — the innermost knows.
    ``bus`` is where the handler is registered: the outermost bus logs the failure, and
    ``failed_in`` still names where it happened.

        remember(KeyError("x"), CreateInvoice(...), "billing", feature, "feature")
            ->  Failure(handler="CreateInvoiceFeature", chain=("CreateInvoice",), ...)
        remember(the same error, ApplyPayment(...), "billing", service, "app_service")
            ->  None                                # the inner handler already recorded it

    Located within the handler's top-level package; kept for `failure_of` only inside an
    open `execution()` — outside one it is returned, not kept."""
    if failure_of(error) is not None:
        return None
    error_at, raised_at = locate(error, type(handler).__module__.partition(".")[0])
    failure = Failure(
        error,
        dto,
        bus,
        type(handler).__name__,
        layer,
        _chain.get(),
        error_at,
        raised_at,
        dict(trace_ids or {}),
    )
    failures = _failures.get()
    if failures is not None:
        failures.append(failure)
    return failure


def annotate(error: BaseException, failure: Failure) -> None:
    """Add the failure's note to `error`, once — the mark `was_reported` looks for."""
    if failure.note not in _notes(error):
        error.add_note(failure.note)


def was_reported(error: BaseException) -> bool:
    """Whether a bus already logged `error` — a `[sincpro]` note on it or on a cause.

    Context: a transport that catches what a bus raised must not log it a second time.
    """
    return any(
        note.startswith(NOTE_PREFIX) for cause in _causes(error) for note in _notes(cause)
    )
