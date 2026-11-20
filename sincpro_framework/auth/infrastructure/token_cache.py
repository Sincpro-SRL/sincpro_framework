"""`TokenCache`: a token fetched once and shared — by every thread, and by every replica when the
running context's store is shared — until shortly before it expires.

    cache = TokenCache()                         the running context's store
    cache.token(("client-credentials", endpoint, client_id, scope), fetch)   ->  IssuedToken

Context: it is the framework's `Cache`, nothing changed: one caller fetches and the others wait
for it (single flight); a token is served until `expires_at - skew`, the skew at most half its
life, never past; the key is made of the grant's parameters and never holds a secret. A token
the API refused (401) is dropped only when it is the one this process last handed out and at
least `min_reuse` old — a resource that refuses every token (an audience misconfigured) costs
one retry per call, never a fetch per request. A token endpoint that asked to wait
(`Retry-After`) is not asked again before then: the same `EndpointUnavailable` is raised at once.
On a shared store, what is cached is the token itself — hand a `seal` (`AesGcmSealer`, `[jwt]`)
to keep it encrypted at rest.
"""

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from pydantic import SecretStr

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.outbound import Sealer
from sincpro_framework.auth.infrastructure.digests import digest, fingerprint
from sincpro_framework.auth.infrastructure.token_endpoint import IssuedToken
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.caching import Cache, CachePolicy, TimeToLive, Validation
from sincpro_framework.data_layer.store import KeyValueStore


class TokenCodec:
    """An `IssuedToken` as the bytes a shared store holds — the secrets' real values, which
    pydantic's JSON would mask, sealed when a `Sealer` is given."""

    def __init__(self, seal: Sealer | None = None) -> None:
        self.seal = seal

    @property
    def schema(self) -> str:
        return "issued-token-1" + ("-sealed" if self.seal else "")

    def encode(self, value: IssuedToken) -> bytes:
        plain = json.dumps(
            {
                **value.model_dump(mode="json", exclude={"access_token", "refresh_token"}),
                "access_token": value.access_token.get_secret_value(),
                "refresh_token": (
                    value.refresh_token.get_secret_value() if value.refresh_token else None
                ),
            }
        ).encode()
        return self.seal.seal(plain) if self.seal else plain

    def decode(self, raw: bytes) -> IssuedToken:
        plain = self.seal.open(raw) if self.seal else raw
        return IssuedToken.model_validate(json.loads(plain))


class TokenStillGood(Validation[IssuedToken]):
    """A cached token holds until `expires_at - skew` — the skew at most half its life."""

    def __init__(self, now: Callable[[], datetime], skew: timedelta) -> None:
        self.now = now
        self.skew = skew

    def after(self, value: IssuedToken, before: str | None) -> str | None:
        life = value.expires_at - value.received_at
        return str((value.expires_at - min(self.skew, life / 2)).timestamp())

    def holds(self, mark: str) -> bool:
        return self.now().timestamp() < float(mark)


class TokenCache:
    """Tokens by the parameters of their grant, on the store given or the running context's."""

    def __init__(
        self,
        store: KeyValueStore | None = None,
        seal: Sealer | None = None,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
        skew: timedelta = timedelta(seconds=30),
        min_reuse: timedelta = timedelta(seconds=10),
        wait_for_others: timedelta = timedelta(seconds=11),
        namespace: str = "auth-tokens",
    ) -> None:
        """`store` pins one instead of the running context's; `wait_for_others` is how long a
        caller waits for the one fetching — at least the token endpoint's timeout."""
        self._store = store
        self.now = now
        self.min_reuse = min_reuse
        self.namespace = namespace
        self._codec = TokenCodec(seal)
        self._policy: CachePolicy[IssuedToken] = CachePolicy(
            freshness=TimeToLive(ttl=timedelta(hours=1)),
            validation=TokenStillGood(now, skew),
            wait_for_others=wait_for_others,
        )
        self._caches: dict[int, Cache] = {}
        self._handed: dict[str, tuple[str, datetime]] = {}
        self._blocked: dict[str, exceptions.EndpointUnavailable] = {}
        self._until: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def _cache(self) -> Cache:
        """The `Cache` on the store given, else on the running context's — one per store."""
        store = (
            self._store if self._store is not None else key_value_store_of(current_store())
        )
        with self._lock:
            if id(store) not in self._caches:
                self._caches[id(store)] = Cache(store, namespace=self.namespace, now=self.now)
            return self._caches[id(store)]

    def _hashed(self, key: tuple[Any, ...]) -> str:
        """The grant's parameters as one digest — what this process tracks a key by."""
        return digest(json.dumps(key, default=str))

    def token(self, key: tuple[Any, ...], fetch: Callable[[], IssuedToken]) -> IssuedToken:
        """The token cached under `key`, fetched by `fetch` when there is none still good.

        1. The endpoint asked to wait and the wait has not passed: its refusal, again.
        2. Answered by the cache — one caller fetches, the others wait for it.
           2.1 The endpoint is unavailable with a Retry-After: remembered, then raised.
        Final: the token, remembered as the one this process handed out.
        """
        hashed = self._hashed(key)
        with self._lock:
            until = self._until.get(hashed)
            if until is not None and self.now() < until:
                raise self._blocked[hashed]
        try:
            issued = self._cache().get_or_compute(key, fetch, self._policy, self._codec)
        except exceptions.EndpointUnavailable as refused:
            if refused.retry_after:
                with self._lock:
                    self._blocked[hashed] = refused
                    self._until[hashed] = self.now() + refused.retry_after
            raise
        with self._lock:
            self._handed[hashed] = (
                fingerprint(issued.access_token.get_secret_value()),
                issued.received_at,
            )
        return issued

    def refused(self, key: tuple[Any, ...], token: str) -> bool:
        """`token` was refused by the API: whether asking again may bring another.

        1. Not the token this process last handed out: another was fetched since — `True`.
        2. Younger than `min_reuse`: a fresh token refused is a configuration to fix — `False`.
        Final: dropped from the cache, for every replica — `True`.
        """
        hashed = self._hashed(key)
        with self._lock:
            handed = self._handed.get(hashed)
        if handed is None or handed[0] != fingerprint(token):
            return True
        if self.now() - handed[1] < self.min_reuse:
            return False
        self._cache().forget(key, self._codec)
        with self._lock:
            self._handed.pop(hashed, None)
        return True


def bearer_of(headers: dict[str, str] | Any, header: str = "authorization") -> str | None:
    """The token of `<scheme> <token>` in `header` of `headers`, if any."""
    value = {name.lower(): one for name, one in dict(headers).items()}.get(header, "")
    _, _, token = value.partition(" ")
    return token.strip() or None


def secret(value: str | SecretStr) -> SecretStr:
    """`value` as a `SecretStr`."""
    return value if isinstance(value, SecretStr) else SecretStr(value)
