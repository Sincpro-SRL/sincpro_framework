"""`KeyValueRecords`: idempotency records on any `KeyValueStore` — Redis/Valkey across replicas.

Context: best effort by construction — the record and the write are two stores, so a replica that
dies between its side effect and `complete` leaves a claim that expires, and the write runs again.
A record is one line of JSON with its bookkeeping, then the encoded answer as it is.
"""

import json
from datetime import timedelta

from sincpro_framework.caching.domain.idempotency_records import (
    IdempotencyRecord,
    IdempotencyRecords,
    RecordState,
)
from sincpro_framework.caching.domain.store import KeyValueStore


def _written(record: IdempotencyRecord) -> bytes:
    header = json.dumps(
        {"state": record.state, "fingerprint": record.fingerprint, "owner": record.owner}
    )
    return header.encode() + b"\n" + record.answer


def _record(held: bytes) -> IdempotencyRecord:
    header, _, answer = held.partition(b"\n")
    about = json.loads(header)
    return IdempotencyRecord(
        state=RecordState(about["state"]),
        fingerprint=about["fingerprint"],
        owner=about["owner"],
        answer=answer,
    )


class KeyValueRecords(IdempotencyRecords):
    def __init__(self, store: KeyValueStore) -> None:
        self.store = store

    def claim(self, key: str, record: IdempotencyRecord, hold: timedelta) -> bool:
        return self.store.add(key, _written(record), hold)

    def read(self, key: str) -> IdempotencyRecord | None:
        held = self.store.get(key)
        return None if held is None else _record(held)

    def complete(self, key: str, record: IdempotencyRecord, keep_for: timedelta) -> None:
        self.store.set(key, _written(record), keep_for)

    def release(self, key: str, owner: str) -> None:
        """Context: read-then-delete is not atomic — `in_progress_for` outliving the run is what
        keeps another caller's claim from being removed here."""
        held = self.read(key)
        if held is not None and held.owner == owner and held.state == RecordState.IN_PROGRESS:
            self.store.delete(key)
