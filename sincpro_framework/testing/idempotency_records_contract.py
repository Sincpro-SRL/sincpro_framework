"""`IdempotencyRecordsContract`: the behaviour every `IdempotencyRecords` owes, as a test class to
inherit.

    class TestOrdersRecords(IdempotencyRecordsContract):
        def make_records(self) -> IdempotencyRecords:
            return OrdersRecords(database)

pytest collects the inherited `test_*` methods; each builds fresh records. Records of yours — a
table completing in the use case's transaction — pass the same class, and then `Idempotency`
runs on them. Nothing here imports pytest.
"""

import threading
from datetime import timedelta

from sincpro_framework.caching.domain.idempotency_records import (
    IdempotencyRecord,
    IdempotencyRecords,
    RecordState,
)

HOLD = timedelta(minutes=1)


def _claim(owner: str, fingerprint: str = "f") -> IdempotencyRecord:
    return IdempotencyRecord(
        state=RecordState.IN_PROGRESS, fingerprint=fingerprint, owner=owner
    )


class IdempotencyRecordsContract:
    """Inherit, answer `make_records` with new, empty records, and run with pytest."""

    racers = 16

    def make_records(self) -> IdempotencyRecords:
        raise NotImplementedError("answer new, empty records")

    def test_only_one_of_many_racing_claims_wins(self) -> None:
        records = self.make_records()
        won: list[bool] = []
        lock = threading.Lock()

        def race(owner: str) -> None:
            claimed = records.claim("k", _claim(owner), HOLD)
            with lock:
                won.append(claimed)

        threads = [threading.Thread(target=race, args=(f"o{n}",)) for n in range(self.racers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if won.count(True) != 1:
            raise AssertionError(f"{won.count(True)} racing claims won; exactly one must")

    def test_a_claim_is_read_back_and_completed(self) -> None:
        records = self.make_records()
        records.claim("k", _claim("owner"), HOLD)
        if records.read("k") != _claim("owner"):
            raise AssertionError("the claim was not read back as written")
        done = _claim("owner").model_copy(
            update={"state": RecordState.COMPLETED, "answer": b'{"n": 1}'}
        )
        records.complete("k", done, HOLD)
        if records.read("k") != done:
            raise AssertionError("the completed record was not read back as written")
        if records.claim("k", _claim("other"), HOLD):
            raise AssertionError("a completed key was claimed again")

    def test_release_removes_only_the_owners_claim(self) -> None:
        records = self.make_records()
        records.claim("k", _claim("owner"), HOLD)
        records.release("k", "somebody-else")
        if records.read("k") is None:
            raise AssertionError("a claim was released by someone who did not write it")
        records.release("k", "owner")
        if records.read("k") is not None or not records.claim("k", _claim("next"), HOLD):
            raise AssertionError("the owner's release did not free the key")

    def test_a_missing_key_reads_as_none(self) -> None:
        if self.make_records().read("never-claimed") is not None:
            raise AssertionError("a key never claimed answered a record")
