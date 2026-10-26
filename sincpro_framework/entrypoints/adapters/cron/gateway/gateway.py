"""`CronGateway`: the in-memory orchestrator — a clock looks, the gateway runs what is due.

    CronGateway([cron_payments, cron_billing]).run()

Context: like `RpcGateway` or `GrpcGateway`, a transport in front of the buses — the crons call
the buses, the buses never learn they were called by a clock. A tick is claimed in `runs` before
anything runs; with a `CronRuns` the replicas share, every replica may look and each tick runs
once. Each run has a thread of its own, so a long cron never delays another's tick; `workers`
caps how many run at once. The ticks of one cron run in order, and with `overlap=SKIP` a tick
that comes while the previous run is still going is recorded as skipped.
"""

from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import BoundedSemaphore, Lock, Thread, current_thread

from sincpro_framework.common.ordering import name_of, refused
from sincpro_framework.entrypoints.adapters.cron.adapters.clocks import InProcessClock
from sincpro_framework.entrypoints.adapters.cron.adapters.in_memory_runs import InMemoryRuns
from sincpro_framework.entrypoints.adapters.cron.domain.clock import Clock
from sincpro_framework.entrypoints.adapters.cron.domain.cron import (
    Cron,
    CronDefinition,
    Missed,
    Overlap,
    Tick,
)
from sincpro_framework.entrypoints.adapters.cron.domain.runs import CronRuns, Run, RunOutcome
from sincpro_framework.entrypoints.adapters.cron.registry import Crons
from sincpro_framework.sincpro_logger import logger

LATE_AFTER = timedelta(minutes=1)
"""A tick this far behind the clock counts as missed, and its `missed` policy decides."""


@dataclass(frozen=True)
class CronStatus:
    """What `CronGateway.status()` answers per cron — a health page, an admin screen.

        status()["cron-payments.Reconcile"]
        ->  CronStatus(last_run=Run(..., outcome=FAILED), last_success=Run(...),
                       next_tick=2026-03-03 02:00-04:00)

    `next_tick` is the trigger's next tick after the last one the gateway saw.
    """

    last_run: Run | None
    last_success: Run | None
    next_tick: datetime


@dataclass(frozen=True)
class _Entry:
    """One cron the gateway runs, with the registry that executes it — several registries
    share one gateway."""

    registry: Crons
    definition: CronDefinition


def _ticks_between(
    definition: CronDefinition, after: datetime, until: datetime
) -> list[datetime]:
    """Every tick of the cron after `after` and up to `until`, inclusive, oldest first.

        _ticks_between(every_15_minutes, after=10:00, until=10:45)  ->  [10:15, 10:30, 10:45]

    Empty when no tick falls in the window.
    """
    ticks: list[datetime] = []
    moment = definition.trigger.next_after(after)
    while moment <= until:
        ticks.append(moment)
        moment = definition.trigger.next_after(moment)
    return ticks


def _ticks_to_run(
    definition: CronDefinition, due: list[datetime], now: datetime
) -> list[datetime]:
    """Context: every due tick but the latest was missed, and so is the latest when it is more
    than `LATE_AFTER` behind. The policy decides which of them still run.

    1. `RUN_ALL`: every due tick inside `missed_window`.
    2. `RUN_LATEST`: the latest, when it is inside `missed_window`.
    3. Final: `SKIP`: the latest only when it is on time.
    """
    if not due:
        return []
    in_window = [tick for tick in due if now - tick <= definition.missed_window]
    if definition.missed == Missed.RUN_ALL:
        return in_window
    if definition.missed == Missed.RUN_LATEST:
        return in_window[-1:]
    return due[-1:] if now - due[-1] <= LATE_AFTER else []


class CronGateway:
    """The orchestrator of one or more `Crons`: a clock looks, it claims and runs what is due.

        gateway = CronGateway([cron_payments, cron_billing], runs=KeyValueRuns(store))
        gateway.run()                           # blocks: InProcessClock looks every 5 s
        gateway.status()                        # last run, last success, next tick per cron
        gateway.run_now(Reconcile)              # "Run Manually"

    Context: building it builds every registry it is given; from then on no cron, dependency
    or replacement can be added to them.
    """

    def __init__(
        self,
        crons: Sequence[Crons],
        runs: CronRuns | None = None,
        workers: int | None = None,
        look_every: timedelta = timedelta(seconds=5),
        jitter: timedelta = timedelta(0),
        clock: Clock | None = None,
    ) -> None:
        """Context: looks every `look_every`, plus up to `jitter` so replicas started together
        do not claim at the same instant. `clock` replaces that loop — a `ManualClock` in tests.

            CronGateway([cron_payments])            ->  InProcessClock every 5 s, InMemoryRuns
            CronGateway([cron_payments], runs=KeyValueRuns(store), workers=2)
            ->  at most two crons running at once, ticks claimed on the shared store

        1. The clock given, else an `InProcessClock(look_every, jitter)`; the record of runs
           given, else an `InMemoryRuns`.
        2. Each registry built, and one entry recorded per cron it defines.
        3. No cursor yet — `run` sets one per cron.
        4. A semaphore of `workers` slots, or none when `workers` is not given.
        Final: nothing running, nothing busy, not stopping.
        """
        self.clock: Clock = clock or InProcessClock(look_every, jitter)
        self.runs: CronRuns = runs or InMemoryRuns()
        self._entries: list[_Entry] = []
        for registry in crons:
            registry.build()
            self._entries += [_Entry(registry, one) for one in registry.definitions]
        self._cursors: dict[str, datetime] = {}
        self._slots: AbstractContextManager = (
            BoundedSemaphore(workers) if workers else nullcontext()
        )
        self._lock = Lock()
        self._threads: set[Thread] = set()
        self._busy: set[str] = set()
        self._stopping = False

    def _fire(self, entry: _Entry, tick: datetime) -> RunOutcome:
        """1. Claim the tick; another replica that claimed it first runs it — skipped here.
        2. A run of an earlier tick still going on another replica and `overlap=SKIP`: record
           the tick as skipped.
        3. Final: run the cron and record the outcome.
        """
        definition = entry.definition
        overlapping = definition.overlap == Overlap.SKIP and self.runs.running(
            definition.name, since=tick - definition.stale_after
        )
        if not self.runs.claim(definition.name, tick):
            return RunOutcome.SKIPPED
        if overlapping:
            self.runs.finish(definition.name, tick, "", RunOutcome.SKIPPED)
            return RunOutcome.SKIPPED
        outcome = entry.registry.execute(definition, Tick(definition.name, tick, self.runs))
        self.runs.finish(definition.name, tick, "", outcome)
        return outcome

    def _fire_in_order(self, entry: _Entry, ticks: list[datetime]) -> None:
        """Context: `execute` never raises, so what fails here is recording the run — the
        database is down. It is logged, and the next tick still runs."""
        with self._slots:
            for tick in ticks:
                try:
                    self._fire(entry, tick)
                except Exception as error:
                    entry.registry.logger.error(
                        f"{entry.definition.name} could not record its run for "
                        f"{tick.isoformat()}: {error!r}"
                    )

    def _serve(self, entry: _Entry, ticks: list[datetime]) -> None:
        """The body of a run's thread: fire its ticks in order, then — whatever happened —
        release the cron (`_busy`) and forget the thread, so `wait` and the next look see it
        done."""
        try:
            self._fire_in_order(entry, ticks)
        finally:
            with self._lock:
                self._busy.discard(entry.definition.name)
                self._threads.discard(current_thread())

    def _skip(self, entry: _Entry, ticks: list[datetime]) -> None:
        """Record as `SKIPPED` the ticks that came while the cron was still running here —
        each claimed first, so a tick another replica claimed is left to it."""
        for tick in ticks:
            if self.runs.claim(entry.definition.name, tick):
                self.runs.finish(entry.definition.name, tick, "", RunOutcome.SKIPPED)

    def _start(self, entry: _Entry, ticks: list[datetime]) -> None:
        """Context: whether a cron already running here skips is decided under the lock, so two
        looks never both start it."""
        name = entry.definition.name
        with self._lock:
            if self._stopping:
                return
            busy = entry.definition.overlap == Overlap.SKIP and name in self._busy
            if not busy:
                self._busy.add(name)
                thread = Thread(target=self._serve, args=(entry, ticks), name=f"cron:{name}")
                self._threads.add(thread)
                thread.start()
        if busy:
            self._skip(entry, ticks)

    def look(self, now: datetime) -> None:
        """Start what is due at `now`. Context: nothing older than `missed_window` can run, so
        the search starts there however long the process was down.

            look(2026-03-02 06:00:03 UTC)   ->  None; Reconcile ("0 6 * * *") started, its
                                                cursor moved to 06:00
            look(2026-03-02 06:00:08 UTC)   ->  None; nothing new is due, nothing started

        Which of the due ticks run is `_ticks_to_run`'s call — the cron's `missed` policy.
        """
        for entry in self._entries:
            name = entry.definition.name
            earliest = max(self._cursors[name], now - entry.definition.missed_window)
            due = _ticks_between(entry.definition, earliest, now)
            if not due:
                continue
            self._cursors[name] = due[-1]
            to_run = _ticks_to_run(entry.definition, due, now)
            if to_run:
                self._start(entry, to_run)

    def run(self) -> None:
        """Start from where each cron last ran — so ticks missed while nothing was running are
        seen — or from now for one that never ran; say what runs and when it ticks next, so a
        process waiting for its first tick is not mistaken for a stuck one; then hand `look` to
        the clock.

            gateway.run()
            ->  logs "1 crons: cron-payments.Reconcile next 2026-03-03T06:00:00+00:00",
                then blocks until `stop`

        With a `ManualClock`, `run` returns at once and each `advance` is a look.
        """
        now = self.clock.now()
        for entry in self._entries:
            last = self.runs.last(entry.definition.name)
            self._cursors[entry.definition.name] = last.scheduled_for if last else now
        next_ticks = ", ".join(
            f"{entry.definition.name} next "
            f"{entry.definition.trigger.next_after(now).isoformat()}"
            for entry in self._entries
        )
        logger.info(f"{len(self._entries)} crons: {next_ticks}")
        self.clock.run(self.look)

    def wait(self) -> None:
        """Until every run started so far has finished.

            clock.advance(...); gateway.wait()   ->  None, once every started run returned

        A run that starts while waiting is joined too — the threads are read again until
        none is left.
        """
        while True:
            with self._lock:
                running = list(self._threads)
            if not running:
                return
            for thread in running:
                thread.join()

    def stop(self) -> None:
        """Stop looking, start nothing more, let the runs in progress finish."""
        self.clock.stop()
        with self._lock:
            self._stopping = True
        self.wait()

    def _entry_of(self, cron_class: type[Cron]) -> _Entry:
        """The entry `cron_class` runs as — refused, with why it may be missing, when none."""
        for entry in self._entries:
            if entry.definition.cron_class is cron_class:
                return entry
        raise refused(
            f"{name_of(cron_class)} is not a cron this gateway runs — its registry switched it "
            "off, replaced it, or is not one of the gateway's"
        )

    def run_now(self, cron_class: type[Cron]) -> RunOutcome:
        """Run a cron at once, in this thread, for a tick at the clock's now — Odoo's "Run
        Manually". Context: it is claimed and recorded like any tick, so it runs once across
        replicas and counts as the cron's latest run: a scheduled tick missed before it is not
        run again after a restart, as `RUN_LATEST` would decide."""
        return self._fire(self._entry_of(cron_class), self.clock.now())

    def plan(self, until: datetime) -> list[tuple[str, datetime]]:
        """Every tick from the clock's now up to `until`, of every cron, in time order — what
        will run, to check a schedule before it is deployed.

            plan(until=now + timedelta(hours=1))
            ->  [("cron-payments.Poll", 10:15), ("cron-billing.Close", 10:30), ...]

        Context: the schedule only — a tick another replica runs, or one `overlap=SKIP` skips,
        is listed as well.
        """
        now = self.clock.now()
        planned = [
            (entry.definition.name, tick)
            for entry in self._entries
            for tick in _ticks_between(entry.definition, now, until)
        ]
        return sorted(planned, key=lambda one: one[1])

    def status(self) -> dict[str, CronStatus]:
        """Each cron's `CronStatus`, by name — its last run and last success from the record
        of runs, and its next tick after the last one this gateway saw (or after now, before
        `run`)."""
        now = self.clock.now()
        return {
            entry.definition.name: CronStatus(
                last_run=self.runs.last(entry.definition.name),
                last_success=self.runs.last_success(entry.definition.name),
                next_tick=entry.definition.trigger.next_after(
                    self._cursors.get(entry.definition.name, now)
                ),
            )
            for entry in self._entries
        }
