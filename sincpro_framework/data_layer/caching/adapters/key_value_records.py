"""`KeyValueRecords`: idempotency records on any `KeyValueStore` — Redis/Valkey across replicas.

Context: best effort by construction — the record and the write are two stores, so a replica that
dies between its side effect and `complete` leaves a claim that expires, and the write runs again.
A record is one line of JSON with its bookkeeping, then the encoded answer as it is.
"""

import json
from datetime import timedelta

from sincpro_framework.data_layer.caching.domain.idempotency_records import (
    IdempotencyRecord,
    IdempotencyRecords,
    RecordState,
)
from sincpro_framework.store import KeyValueStore


def _written(record: IdempotencyRecord) -> bytes:
    """The bytes the store keeps for `record`: a JSON header line, then the answer untouched.

        _written(IdempotencyRecord(state=COMPLETED, fingerprint="9f", owner="r", answer=b"7"))
            ->  b'{"state": "completed", "fingerprint": "9f", "owner": "r"}\\n7'

    The answer stays the bytes the codec wrote — never re-encoded.
    """
    header = json.dumps(
        {"state": record.state, "fingerprint": record.fingerprint, "owner": record.owner}
    )
    return header.encode() + b"\n" + record.answer


def _record(held: bytes) -> IdempotencyRecord:
    """`_written` read back: split on the first newline — the answer may hold any bytes."""
    header, _, answer = held.partition(b"\n")
    about = json.loads(header)
    return IdempotencyRecord(
        state=RecordState(about["state"]),
        fingerprint=about["fingerprint"],
        owner=about["owner"],
        answer=answer,
    )


class KeyValueRecords(IdempotencyRecords):
    """The `IdempotencyRecords` port on a `KeyValueStore` — best effort, no transaction."""

    def __init__(self, store: KeyValueStore) -> None:
        """`store` holds the records; its expiry ends a claim and a completed answer."""
        self.store = store

    def claim(self, key: str, record: IdempotencyRecord, hold: timedelta) -> bool:
        """The store's `add` — atomic on any store that honours the port."""
        return self.store.add(key, _written(record), hold)

    def read(self, key: str) -> IdempotencyRecord | None:
        """A plain `get`, decoded — `None` once the store let the record expire."""
        held = self.store.get(key)
        return None if held is None else _record(held)

    def complete(self, key: str, record: IdempotencyRecord, keep_for: timedelta) -> None:
        """A plain `set` over the claim — nothing checks that this caller still owns it."""
        self.store.set(key, _written(record), keep_for)

    def release(self, key: str, owner: str) -> None:
        """Context: read-then-delete is not atomic — `in_progress_for` outliving the run is what
        keeps another caller's claim from being removed here."""
        held = self.read(key)
        if held is not None and held.owner == owner and held.state == RecordState.IN_PROGRESS:
            self.store.delete(key)
