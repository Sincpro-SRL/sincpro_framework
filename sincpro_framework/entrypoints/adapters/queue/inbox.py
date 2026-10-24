"""The inbox: each delivery claimed before the bus runs, so a redelivered message that already
completed is acknowledged without running again (PRD_15 §5.2).

    inbox:{binding}:{source}:{id}      the delivery — its producer and its CloudEvents id

Context: the same port `@idempotency.once` stands on (`IdempotencyRecords`), at another stage and
keyed by something else — the inbox keys the *delivery* in the wire, `once` keys the *business
request* in the bus. Over `KeyValueRecords` a replica that dies after the use case and before
`complete` runs the message again once the claim expires (best effort); records that commit in
the use case's own transaction make it exactly once within that database.
"""

import asyncio
import hashlib
from datetime import timedelta
from enum import StrEnum
from uuid import uuid4

from sincpro_framework.data_layer.caching import (
    IdempotencyRecord,
    IdempotencyRecords,
    RecordState,
)


class Claimed(StrEnum):
    """What claiming a delivery in the inbox answered — the wire turns each into a verdict."""

    WON = "won"
    """This delivery runs."""
    COMPLETED = "completed"
    """A delivery of the same message completed: acknowledge, do not run."""
    IN_PROGRESS = "in_progress"
    """Another delivery of it is running right now: retry later."""
    KEY_REUSED = "key_reused"
    """The same producer and id with another body: not the same message — dead-letter."""


def inbox_key(binding: str, source: str | None, message_id: str) -> str:
    """The key one context's run of one message is claimed under — a message id is unique
    only per producer, so the producer is part of it.

        inbox_key("billing.IssueInvoice", "urn:svc:sales", "m-1")
        ->  "inbox:billing.IssueInvoice:urn:svc:sales:m-1"
        inbox_key("billing.IssueInvoice", None, "m-1")  ->  "inbox:billing.IssueInvoice::m-1"
    """
    return f"inbox:{binding}:{source or ''}:{message_id}"


def fingerprint_of(body: bytes) -> str:
    """The SHA-256 of a body — how a redelivery is told from a message reusing its id."""
    return hashlib.sha256(body).hexdigest()


class Inbox:
    """The wire's inbox over an `IdempotencyRecords` — claim before the bus, complete after
    it, release on failure.

        claimed, owner = await inbox.claim(key, body)     ->  (Claimed.WON, "3f2a…")
        await inbox.complete(key, body, owner)            # a redelivery is now COMPLETED
    """

    def __init__(
        self, records: IdempotencyRecords, in_progress_for: timedelta, keep_for: timedelta
    ) -> None:
        """`in_progress_for` is how long a claim holds a run; `keep_for` how long a completed
        delivery is remembered."""
        self.records = records
        self.in_progress_for = in_progress_for
        self.keep_for = keep_for

    def _claim(self, key: str, fingerprint: str, owner: str) -> Claimed:
        """1. Claim the key, held `in_progress_for`.
        2. Lost: what holds it decides — the same body completed, running, or another body.
           2.1 Gone between the claim and the read (it expired): claim once more.
        """
        record = IdempotencyRecord(
            state=RecordState.IN_PROGRESS, fingerprint=fingerprint, owner=owner
        )
        for _ in range(2):
            if self.records.claim(key, record, self.in_progress_for):
                return Claimed.WON
            held = self.records.read(key)
            if held is None:
                continue
            if held.fingerprint != fingerprint:
                return Claimed.KEY_REUSED
            if held.state == RecordState.COMPLETED:
                return Claimed.COMPLETED
            return Claimed.IN_PROGRESS
        return Claimed.IN_PROGRESS

    async def claim(self, key: str, body: bytes) -> tuple[Claimed, str]:
        """The claim and its owner — the store is called off the loop."""
        owner = uuid4().hex
        claimed = await asyncio.to_thread(self._claim, key, fingerprint_of(body), owner)
        return claimed, owner

    async def complete(self, key: str, body: bytes, owner: str) -> None:
        """Mark the delivery done, kept for `keep_for` — every redelivery of it until then is
        replayed: acked without running."""
        record = IdempotencyRecord(
            state=RecordState.COMPLETED, fingerprint=fingerprint_of(body), owner=owner
        )
        await asyncio.to_thread(self.records.complete, key, record, self.keep_for)

    async def release(self, key: str, owner: str) -> None:
        """Drop `owner`'s claim after a failure, so the retry runs at once — never a claim
        another delivery won after this one expired."""
        await asyncio.to_thread(self.records.release, key, owner)
