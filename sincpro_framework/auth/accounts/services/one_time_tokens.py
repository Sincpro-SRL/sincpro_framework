"""`OneTimeTokens`: a link that works once — a password reset, an email verification, an
invitation — bound to what it is for, gone once used, and the older ones invalidated when a
newer one is sent.

    links = OneTimeTokens()                                   the running context's store
    token = links.issue("password-reset", "user:ana", ttl=timedelta(minutes=30))
    links.redeem("password-reset", token)     ->  OneTimeTokenRecord(subject="user:ana", …)
    links.redeem("password-reset", token)     ->  raises Unauthenticated: used

Context: stored by the token's digest under its purpose — a reset link is never an invitation —
and taken atomically (`take`), so two clicks at once redeem it once. With `single_outstanding`
(the default), only the last link sent to a subject for a purpose works: asking again
invalidates the one before. The token is never stored as issued; quipu's admin handing a temporary password in
the HTTP answer is what a link sent by email replaces.
"""

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

from pydantic import ConfigDict, Field

from sincpro_framework.auth.accounts.domain.events import (
    OneTimeTokenIssued,
    OneTimeTokenRedeemed,
)
from sincpro_framework.auth.common.adapters.key_value import KeyValueStoreMixin
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.events import SUBJECT, EventPublisher
from sincpro_framework.auth.common.infrastructure.digests import digest, random_token
from sincpro_framework.auth.common.infrastructure.event_publishing import publish_event
from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.sincpro_abstractions import DataTransferObject


class OneTimeTokenRecord(DataTransferObject):
    """What a one-time token was issued for."""

    model_config = ConfigDict(frozen=True)

    purpose: str
    subject: str
    data: dict[str, str] = Field(default_factory=dict)
    issued_at: datetime
    expires_at: datetime
    single_outstanding: bool = True
    """Whether a newer token issued to the subject for the purpose invalidates this one."""


class OneTimeTokens(KeyValueStoreMixin):
    """Single-use tokens per purpose, on a `KeyValueStore`."""

    warn_when_unshared = True

    def __init__(
        self,
        store: KeyValueStore | None = None,
        prefix: str = "sincpro:auth:once",
        clock: Callable[[], float] = time.time,
        publisher: EventPublisher | None = None,
    ) -> None:
        """`store` holds the tokens — the running context's when not given; `prefix` separates
        their keys from others in it; `clock` is the time they expire by; `publisher` is told
        each one issued and redeemed — its purpose, never the token."""
        self._init_key_value_store(store, prefix)
        self.clock = clock
        self.publisher = publisher

    def _key(self, purpose: str, token: str) -> str:
        """Where a token's record is stored: under its purpose and its digest, never the token."""
        return f"{self.prefix}:{purpose}:{digest(token)}"

    def _latest_key(self, purpose: str, subject: str) -> str:
        """Where the digest of the last token issued to `subject` for `purpose` is stored."""
        return f"{self.prefix}:latest:{purpose}:{digest(subject)}"

    def issue(
        self,
        purpose: str,
        subject: str,
        ttl: timedelta,
        data: Mapping[str, str] | None = None,
        single_outstanding: bool = True,
    ) -> str:
        """A new token for `subject` and `purpose`, alive `ttl` — the only one of its purpose
        that works when `single_outstanding`. The token is shown here, once."""
        token = random_token("ot_")
        now = self.clock()
        record = OneTimeTokenRecord(
            purpose=purpose,
            subject=subject,
            data=dict(data or {}),
            issued_at=datetime.fromtimestamp(now, UTC),
            expires_at=datetime.fromtimestamp(now + ttl.total_seconds(), UTC),
            single_outstanding=single_outstanding,
        )
        store = self.store()
        if single_outstanding:
            store.set(self._latest_key(purpose, subject), digest(token).encode(), ttl)
        store.set(self._key(purpose, token), record.model_dump_json().encode(), ttl)
        publish_event(
            self.publisher,
            OneTimeTokenIssued(entity_type=SUBJECT, entity_id=subject, purpose=purpose),
        )
        return token

    def _parse_outstanding(
        self, raw: bytes, purpose: str, token: str
    ) -> OneTimeTokenRecord | None:
        """The record stored for `token` — `None` when a newer token invalidated it."""
        record = OneTimeTokenRecord.model_validate_json(raw)
        if record.single_outstanding:
            latest = self.store().get(self._latest_key(purpose, record.subject))
            if latest is None or latest.decode() != digest(token):
                return None
        return record

    def peek(self, purpose: str, token: str) -> OneTimeTokenRecord | None:
        """What `token` is for, without using it — a screen showing whose reset it is."""
        raw = self.store().get(self._key(purpose, token))
        return None if raw is None else self._parse_outstanding(raw, purpose, token)

    def redeem(self, purpose: str, token: str) -> OneTimeTokenRecord:
        """Use `token` — taken atomically, so once only.

        1. Not there — expired, used, or of another purpose: `Unauthenticated`.
        2. A newer token was issued for the subject and purpose: `Unauthenticated`.
        Final: what it was issued for.
        """
        raw = self.store().take(self._key(purpose, token))
        if raw is None:
            raise exceptions.Unauthenticated("the link expired or was already used")
        record = self._parse_outstanding(raw, purpose, token)
        if record is None:
            raise exceptions.Unauthenticated("a newer link was sent")
        publish_event(
            self.publisher,
            OneTimeTokenRedeemed(
                entity_type=SUBJECT, entity_id=record.subject, purpose=purpose
            ),
        )
        return record

    def invalidate(self, purpose: str, subject: str) -> None:
        """Every outstanding single token of `subject` for `purpose` stops working."""
        self.store().delete(self._latest_key(purpose, subject))
