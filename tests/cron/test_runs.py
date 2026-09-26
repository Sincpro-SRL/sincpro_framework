"""`CronRuns`: the once-per-tick guard, and the record a cron's next tick is decided from.

The contract every implementation keeps, exercised on `InMemoryRuns` — the one the framework
ships. A project that runs its crons on several replicas implements `CronRuns` on the storage
those replicas share, and these are the behaviours it owes.
"""

from datetime import UTC, datetime, timedelta

from sincpro_framework.cron import (
    Cron,
    CronGateway,
    CronRuns,
    Crons,
    InMemoryRuns,
    RunOutcome,
    Tick,
)
from sincpro_framework.testing import ManualClock

TICK = datetime(2026, 9, 26, 2, 0, tzinfo=UTC)


def test_the_first_claim_wins_and_every_other_is_refused():
    runs = InMemoryRuns()

    assert runs.claim("close", TICK) is True
    assert runs.claim("close", TICK) is False
    assert runs.claim("close", TICK, "F-1") is True


def test_a_claimed_run_is_running_until_it_finishes():
    runs = InMemoryRuns()
    runs.claim("close", TICK)
    assert runs.running("close", since=TICK)

    runs.finish("close", TICK, "", RunOutcome.SUCCEEDED)

    assert not runs.running("close", since=TICK)


def test_an_unfinished_run_older_than_since_is_not_running():
    runs = InMemoryRuns()
    runs.claim("close", TICK)

    assert not runs.running("close", since=TICK + timedelta(hours=1))


def test_last_and_last_success_answer_the_latest_ticks():
    runs = InMemoryRuns()
    runs.claim("close", TICK)
    runs.finish("close", TICK, "", RunOutcome.SUCCEEDED)
    runs.claim("close", TICK + timedelta(days=1))
    runs.finish("close", TICK + timedelta(days=1), "", RunOutcome.FAILED)

    last, success = runs.last("close"), runs.last_success("close")

    assert last is not None and last.scheduled_for == TICK + timedelta(days=1)
    assert last.outcome == RunOutcome.FAILED
    assert success is not None and success.scheduled_for == TICK


def test_a_step_claimed_with_once_does_not_count_as_the_crons_own_run():
    runs = InMemoryRuns()
    runs.claim("close", TICK, "F-1")

    assert runs.last("close") is None
    assert not runs.running("close", since=TICK)


def test_a_gateway_started_on_runs_with_history_sees_what_it_missed():
    runs = InMemoryRuns()
    runs.claim("close", TICK)
    runs.finish("close", TICK, "", RunOutcome.SUCCEEDED)
    executed: list[datetime] = []
    crons = Crons("cron-restart")

    @crons.cron("0 2 * * *", timezone="UTC", name="close")
    class Close(Cron):
        def run(self, tick: Tick) -> None:
            executed.append(tick.scheduled_for)

    clock = ManualClock(TICK + timedelta(days=2, minutes=30))
    gateway = CronGateway([crons], clock=clock, runs=runs)
    gateway.run()
    clock.advance(seconds=1)
    gateway.wait()

    assert executed == [TICK + timedelta(days=2)]  # RUN_LATEST, by default


def test_the_contract_is_an_abstract_class_an_implementation_extends():
    assert issubclass(InMemoryRuns, CronRuns)
    assert CronRuns.__abstractmethods__ == {
        "claim",
        "finish",
        "running",
        "last",
        "last_success",
    }


def test_the_package_answers_only_what_a_project_uses():
    import sincpro_framework.cron as cron

    assert sorted(cron.__all__) == [
        "Cron",
        "CronGateway",
        "CronProcess",
        "CronRuns",
        "CronStatus",
        "Crons",
        "InMemoryRuns",
        "Missed",
        "Overlap",
        "Run",
        "RunOutcome",
        "Tick",
    ]
