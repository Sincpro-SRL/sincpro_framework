"""`DatabaseRuns`: the once-per-tick guard shared by every replica through one table.

What `InMemoryRuns` does inside a process, across processes — and across restarts, which is what
lets a gateway see the ticks it missed while it was down.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import MetaData

from sincpro_framework.cron import Cron, CronGateway, Crons, ManualClock, RunOutcome, Tick
from sincpro_framework.cron.database import DatabaseRuns, cron_run_table
from sincpro_framework.orm import Database

TICK = datetime(2026, 9, 26, 2, 0, tzinfo=UTC)


@pytest.fixture
def runs(tmp_path) -> DatabaseRuns:
    metadata = MetaData()
    table = cron_run_table(metadata)
    database = Database(f"sqlite:///{tmp_path / 'runs.sqlite3'}")
    metadata.create_all(database.engine)
    return DatabaseRuns(database, table)


def test_the_first_claim_wins_and_every_other_is_refused(runs: DatabaseRuns):
    assert runs.claim("close", TICK) is True
    assert runs.claim("close", TICK) is False
    assert runs.claim("close", TICK, "F-1") is True


def test_a_claimed_run_is_running_until_it_finishes(runs: DatabaseRuns):
    runs.claim("close", TICK)
    assert runs.running("close", since=TICK)

    runs.finish("close", TICK, "", RunOutcome.SUCCEEDED)

    assert not runs.running("close", since=TICK)


def test_an_unfinished_run_older_than_since_is_not_running(runs: DatabaseRuns):
    runs.claim("close", TICK)

    assert not runs.running("close", since=TICK + timedelta(hours=1))


def test_last_and_last_success_come_back_aware(runs: DatabaseRuns):
    runs.claim("close", TICK)
    runs.finish("close", TICK, "", RunOutcome.SUCCEEDED)
    runs.claim("close", TICK + timedelta(days=1))
    runs.finish("close", TICK + timedelta(days=1), "", RunOutcome.FAILED)

    last, success = runs.last("close"), runs.last_success("close")

    assert last is not None and last.scheduled_for == TICK + timedelta(days=1)
    assert last.outcome == RunOutcome.FAILED
    assert success is not None and success.scheduled_for == TICK
    assert last.scheduled_for.tzinfo is not None


def test_a_step_claimed_with_once_does_not_count_as_the_crons_own_run(runs: DatabaseRuns):
    runs.claim("close", TICK, "F-1")

    assert runs.last("close") is None
    assert not runs.running("close", since=TICK)


def _crons(executed: list[datetime]) -> Crons:
    crons = Crons("cron-database")

    @crons.cron("0 2 * * *", timezone="UTC", name="close")
    class Close(Cron):
        def run(self, tick: Tick) -> None:
            executed.append(tick.scheduled_for)

    return crons


def test_two_replicas_on_one_table_execute_each_tick_once(runs: DatabaseRuns):
    executed: list[datetime] = []
    clock = ManualClock(TICK - timedelta(minutes=1))
    crons = _crons(executed)
    replicas = [CronGateway([crons], clock=clock, runs=runs) for _ in range(2)]
    for replica in replicas:
        replica.run()

    clock.advance(minutes=2)
    for replica in replicas:
        replica.wait()

    assert executed == [TICK]


def test_a_restarted_gateway_sees_what_it_missed(runs: DatabaseRuns):
    runs.claim("close", TICK)
    runs.finish("close", TICK, "", RunOutcome.SUCCEEDED)
    executed: list[datetime] = []
    clock = ManualClock(TICK + timedelta(days=2, minutes=30))
    gateway = CronGateway([_crons(executed)], clock=clock, runs=runs)
    gateway.run()

    clock.advance(seconds=1)
    gateway.wait()

    assert executed == [TICK + timedelta(days=2)]  # RUN_LATEST, by default
