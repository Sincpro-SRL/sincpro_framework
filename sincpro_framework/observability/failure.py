"""What failed in one execution, and where: the facts the log, the span and GlitchTip share.

Context: a command crosses several handlers — an ApplicationService running Features, a
Feature calling another context's bus. The failure is described once, by the handler that
raised it, and reported once, by the outermost bus. State lives in ``ContextVar``s, so
concurrent executions never see each other's failures.
"""

import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Generator, Iterator

FRAMEWORK_PACKAGE = "sincpro_framework"
NOTE_PREFIX = "[sincpro] "


@dataclass(frozen=True)
class CodeLocation:
    module: str
    function: str
    file: str
    line: int

    def __str__(self) -> str:
        return f"{self.module}:{self.line} in {self.function}"


@dataclass(frozen=True)
class Failure:
    error: BaseException
    dto: object
    bus: str
    handler: str
    layer: str
    chain: tuple[str, ...]
    error_at: CodeLocation | None
    raised_at: CodeLocation | None

    @property
    def summary(self) -> str:
        return f"{self.handler} failed: {type(self.error).__name__}: {self.error}"

    @property
    def note(self) -> str:
        where = f" at {self.error_at}" if self.error_at else ""
        path = f"{' → '.join(self.chain)}: " if len(self.chain) > 1 else ""
        who = f"{self.handler} ({self.bus})"
        return f"{NOTE_PREFIX}{path}{who} failed{where} executing {self.dto!r}"

    def fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "dto": repr(self.dto),
            "failed_in": self.bus,
            "handler": self.handler,
            "layer": self.layer,
            "error_type": type(self.error).__name__,
            "chain": " → ".join(self.chain),
        }
        if self.error_at:
            fields["error_at"] = str(self.error_at)
        if self.raised_at:
            fields["raised_at"] = str(self.raised_at)
        return fields


def _belongs_to(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def locate(
    error: BaseException, package: str
) -> tuple[CodeLocation | None, CodeLocation | None]:
    """Context: the log call always sits in the bus, so the logger's own ``filename`` names
    the framework. The traceback names the consumer's code.

    1. ``error_at``: the innermost frame of ``package`` — the handler's own code — or, when
       the handler lives elsewhere, the innermost frame outside the framework.
    2. Final: ``raised_at``: the frame that raised, when it is not ``error_at`` (a SOAP or
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
    """Context: yields whether this is the outermost execution — the only one that reports
    an error escaping it. Inner ones join it."""
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
    token = _chain.set((*_chain.get(), dto_name))
    try:
        yield
    finally:
        _chain.reset(token)


def _causes(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _notes(error: BaseException) -> list[str]:
    return getattr(error, "__notes__", [])


def failure_of(error: BaseException) -> Failure | None:
    """Context: an error handler that maps a SOAP fault to the SDK's own exception still
    leads back to the fault, through ``__cause__`` / ``__context__``."""
    for recorded in reversed(_failures.get() or []):
        if any(cause is recorded.error for cause in _causes(error)):
            return recorded
    return None


def remember(
    error: BaseException, dto: object, bus: str, handler: object, layer: str
) -> Failure | None:
    """Context: ``None`` when an inner handler already recorded it — the innermost knows.
    ``bus`` is where the handler is registered: the outermost bus logs the failure, and
    ``failed_in`` still names where it happened."""
    if failure_of(error) is not None:
        return None
    error_at, raised_at = locate(error, type(handler).__module__.partition(".")[0])
    failure = Failure(
        error, dto, bus, type(handler).__name__, layer, _chain.get(), error_at, raised_at
    )
    failures = _failures.get()
    if failures is not None:
        failures.append(failure)
    return failure


def annotate(error: BaseException, failure: Failure) -> None:
    if failure.note not in _notes(error):
        error.add_note(failure.note)


def was_reported(error: BaseException) -> bool:
    return any(
        note.startswith(NOTE_PREFIX) for cause in _causes(error) for note in _notes(cause)
    )
