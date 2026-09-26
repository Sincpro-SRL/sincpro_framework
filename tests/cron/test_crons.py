"""Crons: a registry per bounded context, one class per cron, the buses it orchestrates injected.

Every case runs on a `ManualClock`: time moves only when the test says so, and `gateway.wait()`
waits for the runs that tick started.
"""

from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from typing import Any

import pytest
from structlog.testing import capture_logs

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.cron import (
    Cron,
    CronGateway,
    Crons,
    InMemoryRuns,
    ManualClock,
    Missed,
    Overlap,
    RunOutcome,
    Tick,
)
from sincpro_framework.exceptions import BusAlreadyBuilt, DependencyNotRegistered

START = datetime(2026, 9, 26, 1, 59, tzinfo=UTC)


class QueryOverdue(DataTransferObject):
    pass


class ResponseOverdue(DataTransferObject):
    invoices: list[str]


class CommandIssue(DataTransferObject):
    invoice_id: str


def _buses(issued: list[tuple[str, dict[str, Any]]]) -> tuple[UseFramework, UseFramework]:
    billing = UseFramework("billing", log_after_execution=False)
    siat = UseFramework("siat", log_after_execution=False)

    @billing.feature(QueryOverdue)
    class Overdue(Feature):
        def execute(self, dto: QueryOverdue) -> ResponseOverdue:
            return ResponseOverdue(invoices=["F-1", "F-2"])

    @siat.feature(CommandIssue)
    class Issue(Feature):
        def execute(self, dto: CommandIssue) -> None:
            issued.append((dto.invoice_id, dict(self.context)))

    return billing, siat


def _crons(issued: list) -> Crons:
    billing, siat = _buses(issued)
    cron_billing = Crons("cron-billing")
    cron_billing.add_dependency("billing", billing)
    cron_billing.add_dependency("siat", siat)

    @cron_billing.cron("0 2 * * *", timezone="UTC")
    class NightlyIssue(Cron):
        billing: UseFramework
        siat: UseFramework

        def run(self, tick: Tick) -> None:
            for invoice in self.billing(QueryOverdue(), ResponseOverdue).invoices:
                self.siat(CommandIssue(invoice_id=invoice))

    return cron_billing


def _running(
    *crons: Crons, clock: ManualClock, runs: InMemoryRuns | None = None
) -> CronGateway:
    gateway = CronGateway(list(crons), clock=clock, runs=runs or InMemoryRuns())
    gateway.run()
    return gateway


# ---------------------------------------------------------------------------------------------
# A cron orchestrates buses
# ---------------------------------------------------------------------------------------------


def test_a_cron_orchestrates_the_buses_it_declares():
    issued: list = []
    clock = ManualClock(START)
    gateway = _running(_crons(issued), clock=clock)

    clock.advance(minutes=2)
    gateway.wait()

    assert [invoice for invoice, _ in issued] == ["F-1", "F-2"]


def test_the_tick_context_reaches_every_bus_the_cron_calls():
    issued: list = []
    clock = ManualClock(START)
    gateway = _running(_crons(issued), clock=clock)

    clock.advance(minutes=2)
    gateway.wait()

    context = issued[0][1]
    assert context["cron"] == "cron-billing.NightlyIssue"
    assert context["scheduled_for"] == "2026-09-26T02:00:00+00:00"


def test_nothing_runs_before_its_time():
    issued: list = []
    clock = ManualClock(START)
    gateway = _running(_crons(issued), clock=clock)

    clock.advance(seconds=30)
    gateway.wait()

    assert issued == []


def test_several_registries_run_in_one_gateway():
    ran: list[str] = []
    first, second = Crons("cron-a"), Crons("cron-b")

    @first.cron(every=timedelta(minutes=1))
    class A(Cron):
        def run(self, tick: Tick) -> None:
            ran.append("a")

    @second.cron(every=timedelta(minutes=1))
    class B(Cron):
        def run(self, tick: Tick) -> None:
            ran.append("b")

    clock = ManualClock(START)
    gateway = _running(first, second, clock=clock)
    clock.advance(minutes=1)
    gateway.wait()

    assert sorted(ran) == ["a", "b"]


def test_every_cron_has_a_thread_of_its_own_so_none_waits_for_another():
    together = Barrier(6, timeout=5)
    crons = Crons("cron-parallel")
    for number in range(6):

        class Waits(Cron):
            def run(self, tick: Tick) -> None:
                together.wait()

        crons.cron(every=timedelta(minutes=1), name=f"waits-{number}")(Waits)

    clock = ManualClock(START)
    gateway = _running(crons, clock=clock)
    clock.advance(minutes=1)
    gateway.wait()

    lasts = [status.last_run for status in gateway.status().values()]
    assert all(run is not None and run.outcome == RunOutcome.SUCCEEDED for run in lasts)


def test_two_replicas_sharing_the_runs_run_each_tick_once():
    ran: list[datetime] = []
    crons = Crons("cron-once")

    @crons.cron("0 2 * * *", timezone="UTC")
    class Close(Cron):
        def run(self, tick: Tick) -> None:
            ran.append(tick.scheduled_for)

    runs, clock = InMemoryRuns(), ManualClock(START)
    replicas = [
        _running(crons, clock=clock, runs=runs),
        _running(crons, clock=clock, runs=runs),
    ]
    clock.advance(minutes=2)
    for replica in replicas:
        replica.wait()

    assert ran == [datetime(2026, 9, 26, 2, 0, tzinfo=UTC)]


def test_once_makes_one_step_of_a_tick_run_at_most_once():
    ran: list[str] = []
    crons = Crons("cron-steps")

    @crons.cron(every=timedelta(minutes=1))
    class Steps(Cron):
        def run(self, tick: Tick) -> None:
            for step in ("a", "a", "b"):
                if tick.once(step):
                    ran.append(step)

    clock = ManualClock(START)
    gateway = _running(crons, clock=clock)
    clock.advance(minutes=1)
    gateway.wait()

    assert ran == ["a", "b"]


# ---------------------------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------------------------


def test_a_failing_run_is_recorded_reported_once_and_the_clock_goes_on():
    attempts: list[datetime] = []
    crons = Crons("cron-failing")

    @crons.cron(every=timedelta(minutes=1))
    class Broken(Cron):
        def run(self, tick: Tick) -> None:
            attempts.append(tick.scheduled_for)
            raise RuntimeError("printer on fire")

    clock = ManualClock(START)
    gateway = _running(crons, clock=clock)
    with capture_logs() as logs:
        clock.advance(minutes=1)
        gateway.wait()
        clock.advance(minutes=1)
        gateway.wait()

    errors = [line for line in logs if line["log_level"] == "error"]
    last = gateway.status()["cron-failing.Broken"].last_run
    assert len(attempts) == 2
    assert len(errors) == 2
    assert errors[0]["app_name"] == "cron-failing"
    assert errors[0]["handler"] == "Broken"
    assert last is not None and last.outcome == RunOutcome.FAILED


def test_a_failure_inside_a_bus_is_logged_once_by_the_cron_naming_where_it_happened():
    crons = Crons("cron-calls")
    siat = UseFramework("siat-down", log_after_execution=False)
    crons.add_dependency("siat", siat)

    @siat.feature(CommandIssue)
    class Issue(Feature):
        def execute(self, dto: CommandIssue) -> None:
            raise ConnectionError("SIAT no responde")

    @crons.cron(every=timedelta(minutes=1))
    class Issuing(Cron):
        siat: UseFramework

        def run(self, tick: Tick) -> None:
            self.siat(CommandIssue(invoice_id="F-1"))

    clock = ManualClock(START)
    gateway = _running(crons, clock=clock)
    with capture_logs() as logs:
        clock.advance(minutes=1)
        gateway.wait()

    [error] = [line for line in logs if line["log_level"] == "error"]
    assert error["app_name"] == "cron-calls"
    assert error["failed_in"] == "siat-down"
    assert error["handler"] == "Issue"


# ---------------------------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------------------------


def _after_downtime(policy: Missed, window: timedelta = timedelta(days=7)) -> list[int]:
    """Ran at 02:00 on the 26th, then the process was down until 02:30 on the 29th."""
    ran: list[int] = []
    crons = Crons("cron-missed")

    @crons.cron("0 2 * * *", timezone="UTC", missed=policy, missed_window=window)
    class Daily(Cron):
        def run(self, tick: Tick) -> None:
            ran.append(tick.scheduled_for.day)

    runs = InMemoryRuns()
    last = datetime(2026, 9, 26, 2, 0, tzinfo=UTC)
    runs.claim("cron-missed.Daily", last)
    runs.finish("cron-missed.Daily", last, "", RunOutcome.SUCCEEDED)
    clock = ManualClock(datetime(2026, 9, 29, 2, 30, tzinfo=UTC))
    gateway = _running(crons, clock=clock, runs=runs)
    clock.advance(seconds=1)
    gateway.wait()
    return ran


def test_missed_ticks_run_latest_only_by_default():
    assert _after_downtime(Missed.RUN_LATEST) == [29]


def test_missed_ticks_can_all_run_in_order():
    assert _after_downtime(Missed.RUN_ALL) == [27, 28, 29]


def test_missed_ticks_can_be_skipped():
    assert _after_downtime(Missed.SKIP) == []


def test_missed_ticks_older_than_the_window_never_run():
    assert _after_downtime(Missed.RUN_ALL, window=timedelta(days=1, hours=12)) == [28, 29]


def test_a_tick_is_skipped_while_the_previous_run_is_still_going():
    ran: list[datetime] = []
    crons = Crons("cron-overlap")

    @crons.cron(every=timedelta(minutes=1), overlap=Overlap.SKIP)
    class Long(Cron):
        def run(self, tick: Tick) -> None:
            ran.append(tick.scheduled_for)

    runs = InMemoryRuns()
    runs.claim("cron-overlap.Long", START - timedelta(minutes=5))  # started, never finished
    clock = ManualClock(START)
    gateway = _running(crons, clock=clock, runs=runs)
    clock.advance(minutes=1)
    gateway.wait()

    last = gateway.status()["cron-overlap.Long"].last_run
    assert ran == []
    assert last is not None and last.outcome == RunOutcome.SKIPPED


def test_a_run_left_unfinished_past_stale_after_no_longer_blocks_its_cron():
    ran: list[datetime] = []
    crons = Crons("cron-stale")

    @crons.cron(every=timedelta(minutes=1), stale_after=timedelta(hours=1))
    class Long(Cron):
        def run(self, tick: Tick) -> None:
            ran.append(tick.scheduled_for)

    runs = InMemoryRuns()
    runs.claim("cron-stale.Long", START - timedelta(hours=2))  # its replica crashed
    clock = ManualClock(START)
    gateway = _running(crons, clock=clock, runs=runs)
    clock.advance(minutes=1)
    gateway.wait()

    assert ran == [START + timedelta(minutes=1)]


def test_the_next_tick_is_skipped_while_this_process_still_runs_the_previous_one():
    ran: list[datetime] = []
    release = Event()
    crons = Crons("cron-busy")

    @crons.cron(every=timedelta(minutes=1))
    class Long(Cron):
        def run(self, tick: Tick) -> None:
            ran.append(tick.scheduled_for)
            release.wait(timeout=5)

    clock = ManualClock(START)
    runs = InMemoryRuns()
    gateway = _running(crons, clock=clock, runs=runs)
    clock.advance(minutes=1)
    clock.advance(minutes=1)
    release.set()
    gateway.wait()

    second = runs.last("cron-busy.Long")
    assert ran == [START + timedelta(minutes=1)]
    assert second is not None and second.outcome == RunOutcome.SKIPPED


def test_with_overlap_allowed_the_ticks_of_one_cron_run_side_by_side():
    both = Barrier(2, timeout=5)
    crons = Crons("cron-allow")

    @crons.cron(every=timedelta(minutes=1), overlap=Overlap.ALLOW)
    class Waits(Cron):
        def run(self, tick: Tick) -> None:
            both.wait()

    clock = ManualClock(START)
    runs = InMemoryRuns()
    gateway = _running(crons, clock=clock, runs=runs)
    clock.advance(minutes=1)
    clock.advance(minutes=1)
    gateway.wait()

    last = runs.last("cron-allow.Waits")
    assert last is not None and last.outcome == RunOutcome.SUCCEEDED


def test_one_worker_still_runs_every_due_cron():
    ran: list[str] = []
    crons = Crons("cron-one-worker")
    for number in range(3):

        class Marks(Cron):
            def run(self, tick: Tick) -> None:
                ran.append(tick.cron)

        crons.cron(every=timedelta(minutes=1), name=f"marks-{number}")(Marks)

    clock = ManualClock(START)
    gateway = CronGateway([crons], clock=clock, runs=InMemoryRuns(), workers=1)
    gateway.run()
    clock.advance(minutes=1)
    gateway.wait()

    assert sorted(ran) == ["marks-0", "marks-1", "marks-2"]


def test_an_every_cron_runs_once_per_interval_on_replicas_started_apart():
    ran: list[datetime] = []
    crons_a, crons_b = Crons("cron-every"), Crons("cron-every")
    for crons in (crons_a, crons_b):

        @crons.cron(every=timedelta(minutes=1), name="sync")
        class Sync(Cron):
            def run(self, tick: Tick) -> None:
                ran.append(tick.scheduled_for)

    runs = InMemoryRuns()
    clock_a, clock_b = ManualClock(START + timedelta(seconds=3)), ManualClock(
        START + timedelta(seconds=7)
    )
    replica_a = _running(crons_a, clock=clock_a, runs=runs)
    replica_b = _running(crons_b, clock=clock_b, runs=runs)
    clock_a.advance(minutes=1)
    clock_b.advance(minutes=1)
    replica_a.wait()
    replica_b.wait()

    assert ran == [START + timedelta(minutes=1)]


class _RunsThatCannotFinish(InMemoryRuns):
    def finish(
        self, name: str, scheduled_for: datetime, key: str, outcome: RunOutcome
    ) -> None:
        raise ConnectionError("database is down")


def test_a_run_whose_outcome_cannot_be_recorded_is_logged_not_lost():
    crons = Crons("cron-unrecorded")

    @crons.cron(every=timedelta(minutes=1))
    class Quick(Cron):
        def run(self, tick: Tick) -> None: ...

    clock = ManualClock(START)
    gateway = _running(crons, clock=clock, runs=_RunsThatCannotFinish())
    with capture_logs() as logs:
        clock.advance(minutes=1)
        gateway.wait()

    errors = [log for log in logs if log["log_level"] == "error"]
    assert errors and "cron-unrecorded.Quick" in errors[0]["event"]


# ---------------------------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------------------------


def test_a_dependency_the_cron_declares_but_the_registry_lacks_fails_the_build():
    crons = Crons("cron-missing")

    @crons.cron(every=timedelta(minutes=1))
    class NeedsSiat(Cron):
        siat: UseFramework

        def run(self, tick: Tick) -> None: ...

    with pytest.raises(DependencyNotRegistered, match="NeedsSiat needs siat.*cron-missing"):
        crons.build()


def test_a_cron_gets_the_dependencies_its_parent_cron_declares():
    issued: list = []
    billing, siat = _buses(issued)
    crons = Crons("cron-inherited")
    crons.add_dependency("billing", billing)
    crons.add_dependency("siat", siat)

    class UsesBilling(Cron):
        billing: UseFramework

        def run(self, tick: Tick) -> None: ...

    @crons.cron(every=timedelta(minutes=1))
    class IssuesOverdue(UsesBilling):
        siat: UseFramework

        def run(self, tick: Tick) -> None:
            for invoice in self.billing(QueryOverdue(), ResponseOverdue).invoices:
                self.siat(CommandIssue(invoice_id=invoice))

    clock = ManualClock(START)
    gateway = _running(crons, clock=clock)
    clock.advance(minutes=1)
    gateway.wait()

    assert [invoice for invoice, _ in issued] == ["F-1", "F-2"]


def test_a_cron_registered_after_the_build_is_refused():
    crons = Crons("cron-late")
    crons.build()

    with pytest.raises(BusAlreadyBuilt, match="cron Late"):

        @crons.cron(every=timedelta(minutes=1))
        class Late(Cron):
            def run(self, tick: Tick) -> None: ...


def test_an_expression_needs_a_timezone_and_exactly_one_trigger_is_given():
    crons = Crons("cron-triggers")

    with pytest.raises(ValueError, match="timezone"):
        crons.cron("0 2 * * *")
    with pytest.raises(ValueError, match="one of"):
        crons.cron("0 2 * * *", timezone="UTC", every=timedelta(minutes=1))
    with pytest.raises(ValueError, match="one of"):
        crons.cron()


def test_two_crons_with_the_same_name_are_refused():
    crons = Crons("cron-names")

    @crons.cron(every=timedelta(minutes=1), name="sweep")
    class First(Cron):
        def run(self, tick: Tick) -> None: ...

    with pytest.raises(ValueError, match="sweep.*already"):

        @crons.cron(every=timedelta(minutes=5), name="sweep")
        class Second(Cron):
            def run(self, tick: Tick) -> None: ...


# ---------------------------------------------------------------------------------------------
# Seeing it
# ---------------------------------------------------------------------------------------------


def test_plan_and_status():
    crons = Crons("cron-seen")

    @crons.cron("0 2 * * *", timezone="UTC")
    class Close(Cron):
        def run(self, tick: Tick) -> None: ...

    clock = ManualClock(START)
    gateway = _running(crons, clock=clock)

    assert [tick.day for _, tick in gateway.plan(until=START + timedelta(days=2))] == [26, 27]

    clock.advance(minutes=2)
    gateway.wait()
    status = gateway.status()["cron-seen.Close"]

    assert status.last_run is not None and status.last_run.outcome == RunOutcome.SUCCEEDED
    assert status.last_success == status.last_run
    assert status.next_tick == datetime(2026, 9, 27, 2, 0, tzinfo=UTC)
