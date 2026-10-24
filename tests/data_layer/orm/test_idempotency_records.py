"""A write run exactly once: the claim seen by every replica at once, the answer committed with the
write — so a write that fails, or a completion that fails, leaves nothing behind and the retry
runs it, and a write that committed is never run again."""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.data_layer.caching import AlreadyInProgress
from sincpro_framework.data_layer.caching.domain.idempotency_records import (
    IdempotencyRecord,
    RecordState,
)
from sincpro_framework.data_layer.orm import (
    DatabaseIdempotencyRecords,
    Repository,
    template_table,
)
from sincpro_framework.runtime.testing import IdempotencyRecordsContract, ManualClock

from .engines import fresh
from .ledger_models import Owner, bank, declare

records_table = template_table.idempotency_table("idempotency_record", bank.metadata)


class TestDatabaseIdempotencyRecords(IdempotencyRecordsContract):
    """The port's contract, racing on a file — its own connection per claim, as on a server."""

    def make_records(self) -> DatabaseIdempotencyRecords:
        declare()
        path = Path(tempfile.mkdtemp()) / "records.sqlite3"
        return DatabaseIdempotencyRecords(
            fresh(f"sqlite:///{path}", bank.metadata), records_table
        )


class CommandOpen(DataTransferObject):
    request_id: str
    name: str

    def idempotency_key(self) -> str:
        return self.request_id


class ResponseOpen(DataTransferObject):
    owner_id: str


@pytest.fixture
def ledger(engine_url: str) -> Repository:
    declare()
    return Repository(fresh(engine_url, bank.metadata))


@pytest.fixture
def records(ledger: Repository) -> DatabaseIdempotencyRecords:
    return DatabaseIdempotencyRecords(ledger.database, records_table)


def opening(
    name: str, ledger: Repository, records: DatabaseIdempotencyRecords, fails: list[str]
) -> UseFramework:
    """A bus whose `Open` saves an owner exactly once — and fails after the save while `fails`
    holds something."""
    bus = UseFramework(name, log_after_execution=False)
    bus.add_dependency("repository", ledger)

    @bus.feature(CommandOpen)
    @bus.idempotency.exactly_once(records)
    class Open(Feature):
        repository: Repository

        def execute(self, dto: CommandOpen) -> ResponseOpen:
            with self.repository.context() as unit:
                owner = Owner(name=dto.name)
                unit.save(owner)
            if fails:
                raise RuntimeError(fails.pop())
            return ResponseOpen(owner_id=owner.id)

    return bus


def test_a_write_runs_once_and_its_answer_is_replayed(ledger, records):
    bus = opening("exactly-once", ledger, records, [])

    first = bus(CommandOpen(request_id="r-1", name="ana"), ResponseOpen)
    again = bus(CommandOpen(request_id="r-1", name="ana"), ResponseOpen)

    assert first == again
    assert ledger.count(Owner).value == 1
    held = records.read(next(iter(_keys(records))))
    assert held is not None and held.state == RecordState.COMPLETED


def test_a_write_that_fails_leaves_nothing_and_the_retry_runs_it(ledger, records):
    bus = opening("exactly-once-fails", ledger, records, ["the bank is down"])

    with pytest.raises(RuntimeError):
        bus(CommandOpen(request_id="r-2", name="bea"))
    assert ledger.count(Owner).value == 0  # the save joined the unit that rolled back
    assert _keys(records) == []  # the claim released

    bus(CommandOpen(request_id="r-2", name="bea"), ResponseOpen)
    assert ledger.count(Owner).value == 1


def test_an_answer_that_cannot_be_completed_rolls_the_write_back(
    ledger, records, monkeypatch
):
    """What `KeyValueRecords` cannot promise: the record and the write are one commit, so a
    completion that fails — a crash before it, alike — leaves no write to run twice."""
    bus = opening("exactly-once-complete", ledger, records, [])

    def lost(key: str, record: IdempotencyRecord, keep_for: timedelta) -> None:
        raise ConnectionError("the replica died before its commit")

    monkeypatch.setattr(records, "complete", lost)
    with pytest.raises(ConnectionError):
        bus(CommandOpen(request_id="r-3", name="cai"))

    assert ledger.count(Owner).value == 0
    monkeypatch.undo()
    bus(CommandOpen(request_id="r-3", name="cai"), ResponseOpen)
    assert ledger.count(Owner).value == 1


def test_a_duplicate_in_flight_is_refused_at_once(tmp_path):
    declare()
    ledger = Repository(fresh(f"sqlite:///{tmp_path / 'ledger.sqlite3'}", bank.metadata))
    records = DatabaseIdempotencyRecords(ledger.database, records_table)
    bus = UseFramework("exactly-once-in-flight", log_after_execution=False)
    refused: list[str] = []

    @bus.feature(CommandOpen)
    @bus.idempotency.exactly_once(records)
    class Open(Feature):
        def execute(self, dto: CommandOpen) -> ResponseOpen:
            try:
                bus(dto)  # the same request again, while this one runs
            except AlreadyInProgress as error:
                refused.append(type(error).__name__)
            return ResponseOpen(owner_id="o-1")

    bus(CommandOpen(request_id="r-4", name="dan"))

    assert refused == ["AlreadyInProgress"]


def test_a_claim_past_its_hold_is_taken_over_and_an_answer_past_its_life_is_gone(ledger):
    clock = ManualClock(datetime(2026, 10, 24, tzinfo=UTC))
    records = DatabaseIdempotencyRecords(ledger.database, records_table, now=clock.now)
    claim = IdempotencyRecord(state=RecordState.IN_PROGRESS, fingerprint="f", owner="dead")

    assert records.claim("k", claim, timedelta(minutes=1))
    assert not records.claim(
        "k", claim.model_copy(update={"owner": "next"}), timedelta(minutes=1)
    )
    clock.advance(minutes=2)
    assert records.read("k") is None
    assert records.claim(
        "k", claim.model_copy(update={"owner": "next"}), timedelta(minutes=1)
    )
    held = records.read("k")
    assert held is not None and held.owner == "next"


def _keys(records: DatabaseIdempotencyRecords) -> list[str]:
    with records.database.session() as session:
        return [row.key for row in session.execute(records.table.select())]
