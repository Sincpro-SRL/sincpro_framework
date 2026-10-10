"""`IdempotencyRecords`: where the record under each idempotency key lives — the port.

    claim(key, record, hold)    atomic: exactly one of many racing callers wins the key
    read(key)                   the record, or `None`
    complete(key, record, keep_for)
    release(key, owner)         only the claim `owner` wrote

Context: the store decides how strong "once" is. On a key-value store (`KeyValueRecords`) a
replica that dies after its side effect and before `complete` leaves a claim that expires and the
write runs again — best effort. A store whose `complete` joins the use case's own transaction
(the ORM's) makes the record and the write one commit, and closes that gap: implement this port
over your database and prove it with `sincpro_framework.runtime.testing.IdempotencyRecordsContract`.
"""

from abc import ABC, abstractmethod
from datetime import timedelta
from enum import StrEnum

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject


class RecordState(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class IdempotencyRecord(DataTransferObject):
    model_config = ConfigDict(frozen=True)

    state: RecordState
    fingerprint: str
    """What `KeyReused` compares: the hashed payload the key was first used with."""
    owner: str
    """Who claimed the key — only its owner releases it."""
    answer: bytes = b""
    """The completed answer, encoded — empty while in progress."""


class IdempotencyRecords(ABC):
    @abstractmethod
    def claim(self, key: str, record: IdempotencyRecord, hold: timedelta) -> bool:
        """Write `record` only when `key` holds nothing, atomically, for `hold` — `True` when
        this caller won the key."""

    @abstractmethod
    def read(self, key: str) -> IdempotencyRecord | None: ...

    @abstractmethod
    def complete(self, key: str, record: IdempotencyRecord, keep_for: timedelta) -> None:
        """Replace the claim with the completed record, kept for `keep_for`."""

    @abstractmethod
    def release(self, key: str, owner: str) -> None:
        """Remove the claim `owner` wrote — never one another caller won after it expired."""
