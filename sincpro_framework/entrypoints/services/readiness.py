"""`ReadinessChecks`: what a service must reach before it takes traffic — a binary, a key, a store, a
service it calls — each a plain callable by name, each bounded in time.

    checks = ReadinessChecks()
    checks.add("tesseract", lambda: shutil.which("tesseract") is not None)
    checks.add("valkey", valkey.ping, timeout=timedelta(milliseconds=300))
    checks.report()
    ->  ReadinessReport(ready=False, checks=[CheckReport(name="tesseract", ready=True, …),
                                             CheckReport(name="valkey", ready=False,
                                                         detail="did not answer within 0.3s", …)])

Context: a check is ready unless it raises (its type and message are the detail) or answers
`False`; anything else it answers is ready. Every check runs at once, each in a thread of its own,
and each is waited for until its own timeout — so a report takes as long as the slowest timeout,
never their sum. A check that does not answer is not ready and keeps running; the next report
waits on that same run, up to its timeout again, instead of starting another — so a check that
hangs holds one thread, not one per probe, and is seen ready as soon as it answers.
"""

import threading
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Self

from sincpro_framework.common.failures import problem_of
from sincpro_framework.entrypoints.domain.exceptions import NotReady
from sincpro_framework.entrypoints.domain.readiness import CheckReport, ReadinessReport

DEFAULT_TIMEOUT = timedelta(seconds=1)
"""The longest a check is waited for when none is given — Kubernetes' own probe timeout."""

type Check = Callable[[], object]
"""A readiness check: ready unless it raises or answers `False`."""


class CheckRun:
    """One run of a check, in a thread of its own: what it answered or raised, once it did."""

    def __init__(self, name: str, check: Check, timeout: timedelta) -> None:
        """Start running `check`; each report waits for it up to `timeout`."""
        self.name = name
        self.timeout = timeout
        self.answer: object = None
        self.error: Exception | None = None
        self.started = time.monotonic()
        self.thread = threading.Thread(
            target=self.run, args=(check,), name=f"readiness:{name}", daemon=True
        )
        self.thread.start()

    def run(self, check: Check) -> None:
        """Run `check`, keeping what it answered or raised."""
        try:
            self.answer = check()
        except Exception as error:
            self.error = error

    def report(self, since: float) -> CheckReport:
        """Wait for it up to its timeout from `since` — when the report asking began, on
        `time.monotonic()` — and say how it answered.

        1. Still running after its timeout: not ready, the timeout as its time.
        2. Raised: not ready, the error's type and message as the detail.
        3. Answered `False`: not ready.
        Final: ready.
        """
        self.thread.join(max(0.0, since + self.timeout.total_seconds() - time.monotonic()))
        seconds = round(time.monotonic() - self.started, 3)
        if self.thread.is_alive():
            return CheckReport(
                name=self.name,
                ready=False,
                detail=f"did not answer within {self.timeout.total_seconds()}s",
                seconds=self.timeout.total_seconds(),
            )
        if self.error is not None:
            return CheckReport(
                name=self.name,
                ready=False,
                detail=f"{type(self.error).__name__}: {self.error}",
                seconds=seconds,
            )
        if self.answer is False:
            return CheckReport(
                name=self.name,
                ready=False,
                detail="the check answered False",
                seconds=seconds,
            )
        return CheckReport(name=self.name, ready=True, seconds=seconds)


class ReadinessChecks:
    """The readiness checks of a service, by name."""

    def __init__(self, timeout: timedelta = DEFAULT_TIMEOUT) -> None:
        """`timeout` is how long a check added without one is waited for."""
        self.timeout = timeout
        self.checks: dict[str, tuple[Check, timedelta]] = {}
        self.running: dict[str, CheckRun] = {}
        self._lock = threading.Lock()

    def add(self, name: str, check: Check, timeout: timedelta | None = None) -> Self:
        """Check `check` before taking traffic, as `name`, waited for `timeout` — the
        default when `None`. A name added again is replaced."""
        self.checks[name] = (check, timeout or self.timeout)
        return self

    def remove(self, name: str) -> Self:
        """Stop checking `name`; one never added is not an error."""
        self.checks.pop(name, None)
        return self

    def report(self) -> ReadinessReport:
        """Every check, run at once, as it answered — ready when each one is.

        1. Each check started in a thread of its own — or, when its run of an earlier report is
           still going, that run, never a second one.
        2. Each waited for up to its own timeout — all at once, so the report takes the slowest
           timeout, never their sum.
        Final: the runs that answered let go, so the next report runs them again.
        """
        with self._lock:
            runs = [
                self.running.get(name) or CheckRun(name, check, timeout)
                for name, (check, timeout) in self.checks.items()
            ]
            self.running = {run.name: run for run in runs}
        since = time.monotonic()
        reports = [run.report(since) for run in runs]
        with self._lock:
            for run in runs:
                if not run.thread.is_alive() and self.running.get(run.name) is run:
                    del self.running[run.name]
        return ReadinessReport(ready=all(one.ready for one in reports), checks=reports)

    def answer(self) -> tuple[int, dict[str, Any]]:
        """`report()` as an HTTP probe reads it — what every HTTP wire's readiness route answers.

        checks.answer()  ->  (200, {"ready": True, "checks": [...]})
        <valkey down>    ->  (503, {"kind": "unavailable", "status": 503,
                                    "detail": "not ready: valkey (...)", "checks": [...], …})
        """
        report = self.report()
        if report.ready:
            return 200, report.model_dump(mode="json")
        return 503, problem_of(NotReady(report)).body()
