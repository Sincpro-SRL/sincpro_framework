"""A cron is extended the way a Feature or a hook is: by reference. A project on top of a core
registry replaces one — keeping its name, so its history of runs goes on — or switches it off;
and whoever operates it runs one now, by hand, as Odoo's "Run Manually" or Hangfire's trigger.
What works, only not as said, is a warning; only running what the gateway does not have is refused.
"""

from datetime import UTC, datetime

import pytest
from structlog.testing import capture_logs

from sincpro_framework.cron import Cron, CronGateway, Crons, InMemoryRuns, RunOutcome, Tick
from sincpro_framework.exceptions import BusAlreadyBuilt, ExtensionRefused
from sincpro_framework.testing import ManualClock

START = datetime(2026, 9, 26, 1, 59, tzinfo=UTC)


class _Recording(Cron):
    ran: list[str]


def _core(ran: list[str]) -> tuple[Crons, type[Cron], type[Cron]]:
    crons = Crons("cron-billing")
    crons.add_dependency("ran", ran)

    @crons.cron("0 2 * * *", timezone="UTC")
    class Reconcile(_Recording):
        def run(self, tick: Tick) -> None:
            self.ran.append("reconcile")

    @crons.cron("0 2 * * *", timezone="UTC")
    class Remind(_Recording):
        def run(self, tick: Tick) -> None:
            self.ran.append("remind")

    return crons, Reconcile, Remind


def _after_the_tick(crons: Crons, runs: InMemoryRuns | None = None) -> CronGateway:
    clock = ManualClock(START)
    gateway = CronGateway([crons], clock=clock, runs=runs or InMemoryRuns())
    gateway.run()
    clock.advance(minutes=2)
    gateway.wait()
    return gateway


def test_a_replacement_runs_instead_and_keeps_the_name_of_what_it_replaces():
    ran: list[str] = []
    crons, reconcile, _remind = _core(ran)

    @crons.cron("0 2 * * *", timezone="UTC", replaces=reconcile)
    class ReconcileWithTheBank(_Recording):
        def run(self, tick: Tick) -> None:
            self.ran.append("reconcile with the bank")

    gateway = _after_the_tick(crons)

    assert sorted(ran) == ["reconcile with the bank", "remind"]
    assert "cron-billing.Reconcile" in gateway.status()
    assert [one.cron_class for one in crons.definitions] == [ReconcileWithTheBank, _remind]


def test_a_replacement_may_tick_on_its_own_schedule():
    ran: list[str] = []
    crons, reconcile, _remind = _core(ran)

    @crons.cron("0 5 * * *", timezone="UTC", replaces=reconcile)
    class ReconcileLater(_Recording):
        def run(self, tick: Tick) -> None:
            self.ran.append("later")

    _after_the_tick(crons)

    assert ran == ["remind"]


def _warnings(logs: list) -> list[str]:
    return [line["event"] for line in logs if line["log_level"] == "warning"]


def test_replacing_a_cron_that_is_not_registered_runs_it_as_its_own_with_a_warning():
    ran: list[str] = []
    crons, _reconcile, _remind = _core(ran)

    class Stranger(_Recording):
        def run(self, tick: Tick) -> None: ...

    with capture_logs() as logs:

        @crons.cron("0 2 * * *", timezone="UTC", replaces=Stranger)
        class ReplacesAStranger(_Recording):
            def run(self, tick: Tick) -> None:
                self.ran.append("replaces a stranger")

    _after_the_tick(crons)

    assert sorted(ran) == ["reconcile", "remind", "replaces a stranger"]
    assert "not registered" in _warnings(logs)[0]


def test_replacing_a_replaced_cron_replaces_the_latest_with_a_warning():
    ran: list[str] = []
    crons, reconcile, _remind = _core(ran)

    @crons.cron("0 2 * * *", timezone="UTC", replaces=reconcile)
    class Second(_Recording):
        def run(self, tick: Tick) -> None:
            self.ran.append("second")

    with capture_logs() as logs:

        @crons.cron("0 2 * * *", timezone="UTC", replaces=reconcile)
        class Third(_Recording):
            def run(self, tick: Tick) -> None:
                self.ran.append("third")

    gateway = _after_the_tick(crons)

    assert sorted(ran) == ["remind", "third"]
    assert "cron-billing.Reconcile" in gateway.status()
    assert "Second already replaced" in _warnings(logs)[0]


def test_a_cron_switched_off_never_runs():
    ran: list[str] = []
    crons, _reconcile, remind = _core(ran)

    crons.without(remind)
    gateway = _after_the_tick(crons)

    assert ran == ["reconcile"]
    assert list(gateway.status()) == ["cron-billing.Reconcile"]


def test_switching_off_a_cron_that_is_not_registered_does_nothing_with_a_warning():
    ran: list[str] = []
    crons, _reconcile, _remind = _core(ran)

    class Stranger(_Recording):
        def run(self, tick: Tick) -> None: ...

    with capture_logs() as logs:
        crons.without(Stranger)
    _after_the_tick(crons)

    assert sorted(ran) == ["reconcile", "remind"]
    assert "not registered" in _warnings(logs)[0]


def test_switching_off_a_cron_once_built_is_refused():
    crons, _reconcile, remind = _core([])
    crons.build()

    with pytest.raises(BusAlreadyBuilt):
        crons.without(remind)


def test_running_a_cron_now_runs_it_once_and_records_the_run():
    ran: list[str] = []
    crons, reconcile, _remind = _core(ran)
    clock = ManualClock(START)
    gateway = CronGateway([crons], clock=clock)
    gateway.run()

    outcome = gateway.run_now(reconcile)

    assert outcome == RunOutcome.SUCCEEDED and ran == ["reconcile"]
    last = gateway.status()["cron-billing.Reconcile"].last_run
    assert last is not None and last.scheduled_for == START


def test_running_now_a_cron_the_gateway_does_not_run_is_refused():
    crons, _reconcile, _remind = _core([])

    class Stranger(_Recording):
        def run(self, tick: Tick) -> None: ...

    gateway = CronGateway([crons], clock=ManualClock(START))

    with pytest.raises(ExtensionRefused, match="Stranger"):
        gateway.run_now(Stranger)


def test_running_now_twice_at_the_same_instant_runs_once():
    ran: list[str] = []
    crons, reconcile, _remind = _core(ran)
    gateway = CronGateway([crons], clock=ManualClock(START))

    first, second = gateway.run_now(reconcile), gateway.run_now(reconcile)

    assert (first, second) == (RunOutcome.SUCCEEDED, RunOutcome.SKIPPED)
    assert ran == ["reconcile"]


def test_the_scheduled_tick_still_runs_after_a_manual_run():
    ran: list[str] = []
    crons, reconcile, _remind = _core(ran)
    clock = ManualClock(START)
    gateway = CronGateway([crons], clock=clock)
    gateway.run()
    gateway.run_now(reconcile)

    clock.advance(minutes=2)
    gateway.wait()

    assert sorted(ran) == ["reconcile", "reconcile", "remind"]
