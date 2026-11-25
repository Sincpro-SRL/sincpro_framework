"""`OneTimeTokens`: a link that works once — a password reset, an email verification, an
invitation — bound to what it is for, gone once used, and the older ones void when a newer one
is sent.

    links = OneTimeTokens()                                   the running context's store
    token = links.issue("password-reset", "user:ana", ttl=timedelta(minutes=30))
    links.redeem("password-reset", token)     ->  OneTimeGrant(subject="user:ana", …)
    links.redeem("password-reset", token)     ->  raises Unauthenticated: used

Context: stored by the token's digest under its purpose — a reset link is never an invitation —
and taken atomically (`take`), so two clicks at once redeem it once. With `single_outstanding`
(the default), only the last link sent to a subject for a purpose works: asking again voids the
one before. The token is never stored as issued; quipu's admin handing a temporary password in
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
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.events import SUBJECT, EventPublisher
from sincpro_framework.auth.common.infrastructure.announcing import announce
from sincpro_framework.auth.common.infrastructure.digests import digest, random_token
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.sincpro_abstractions import DataTransferObject


class OneTimeGrant(DataTransferObject):
    """What a one-time token was issued for."""

    model_config = ConfigDict(frozen=True)

    purpose: str
    subject: str
    data: dict[str, str] = Field(default_factory=dict)
    issued_at: datetime
    expires_at: datetime
    single_outstanding: bool = True
    """Whether a newer token issued to the subject for the purpose voids this one."""


class OneTimeTokens:
    """Single-use tokens per purpose, on a `KeyValueStore`."""

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
        self._store = store
        self.prefix = prefix
        self.clock = clock
        self.publisher = publisher

    def store(self) -> KeyValueStore:
        """The store the tokens are kept in — the one given, else the running context's."""
        return self._store if self._store is not None else key_value_store_of(current_store())

    def _key(self, purpose: str, token: str) -> str:
        """Where a token's grant is kept: under its purpose and its digest, never the token."""
        return f"{self.prefix}:{purpose}:{digest(token)}"

    def _latest_key(self, purpose: str, subject: str) -> str:
        """Where the digest of the last token issued to `subject` for `purpose` is kept."""
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
        grant = OneTimeGrant(
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
        store.set(self._key(purpose, token), grant.model_dump_json().encode(), ttl)
        announce(
            self.publisher,
            OneTimeTokenIssued(entity_type=SUBJECT, entity_id=subject, purpose=purpose),
        )
        return token

    def _grant(self, raw: bytes, purpose: str, token: str) -> OneTimeGrant | None:
        """The grant a stored record holds — `None` when a newer one voided it."""
        grant = OneTimeGrant.model_validate_json(raw)
        if grant.single_outstanding:
            latest = self.store().get(self._latest_key(purpose, grant.subject))
            if latest is None or latest.decode() != digest(token):
                return None
        return grant

    def peek(self, purpose: str, token: str) -> OneTimeGrant | None:
        """What `token` is for, without using it — a screen showing whose reset it is."""
        raw = self.store().get(self._key(purpose, token))
        return None if raw is None else self._grant(raw, purpose, token)

    def redeem(self, purpose: str, token: str) -> OneTimeGrant:
        """Use `token` — taken atomically, so once only.

        1. Not there — expired, used, or of another purpose: `Unauthenticated`.
        2. A newer token was issued for the subject and purpose: `Unauthenticated`.
        Final: what it was issued for.
        """
        raw = self.store().take(self._key(purpose, token))
        if raw is None:
            raise exceptions.Unauthenticated("the link expired or was already used")
        grant = self._grant(raw, purpose, token)
        if grant is None:
            raise exceptions.Unauthenticated("a newer link was sent")
        announce(
            self.publisher,
            OneTimeTokenRedeemed(
                entity_type=SUBJECT, entity_id=grant.subject, purpose=purpose
            ),
        )
        return grant

    def void(self, purpose: str, subject: str) -> None:
        """Every outstanding single token of `subject` for `purpose` stops working."""
        self.store().delete(self._latest_key(purpose, subject))
