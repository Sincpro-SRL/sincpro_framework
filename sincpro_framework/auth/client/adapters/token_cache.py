"""`TokenCache`: a token fetched once and shared — by every thread, and by every replica when the
running context's store is shared — until shortly before it expires.

    cache = TokenCache()                         the running context's store
    cache.token(("client_credentials", endpoint, client_id, scope), fetch)   ->  TokenResponse

Context: it is the framework's `Cache`, nothing changed: one caller fetches and the others wait
for it (single flight); a token is served until `token_renewal_time` — `expires_at - skew`, the
skew at most half its life; the key is made of the grant's parameters and never holds a secret.
A token the API refused (401) is dropped only when it is still the one cached and at least
`refetch_grace` old — a resource that refuses every token (an audience misconfigured) costs one
retry per call, never a fetch per request. It is judged against what the cache holds, never
against what this object handed out, so a source built again (a tenant's, evicted and resolved
anew) or another replica drops it all the same. A token endpoint that asked to wait
(`Retry-After`) is not asked again before then: the same `EndpointUnavailable` is raised at once,
and forgotten once the wait is over. On a shared store, what is cached is the token itself — give
a `sealer` (`AesGcmSealer`, `[jwt]`) to store it encrypted.
"""

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sincpro_framework.auth.client.domain.outbound import Sealer
from sincpro_framework.auth.client.domain.token_response import (
    TokenResponse,
    token_renewal_time,
)
from sincpro_framework.auth.client.infrastructure.sealed_codec import SealedJsonCodec
from sincpro_framework.auth.common.adapters.key_value import KeyValueStoreMixin
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.infrastructure.digests import digest
from sincpro_framework.data_layer.caching import Cache, CachePolicy, TimeToLive, Validation
from sincpro_framework.data_layer.store import KeyValueStore


class TokenValidity(Validation[TokenResponse]):
    """A cached token holds until its `token_renewal_time`."""

    def __init__(self, now: Callable[[], datetime], skew: timedelta) -> None:
        self.now = now
        self.skew = skew

    def after(self, value: TokenResponse, before: str | None) -> str | None:
        """The mark: the moment the token stops being handed out."""
        return str(
            token_renewal_time(value.received_at, value.expires_at, self.skew).timestamp()
        )

    def holds(self, mark: str) -> bool:
        """Whether that moment has not come yet."""
        return self.now().timestamp() < float(mark)


class TokenCache(KeyValueStoreMixin):
    """Tokens by the parameters of their grant, on the store given or the running context's."""

    def __init__(
        self,
        store: KeyValueStore | None = None,
        sealer: Sealer | None = None,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
        skew: timedelta = timedelta(seconds=30),
        refetch_grace: timedelta = timedelta(seconds=10),
        fetch_wait_timeout: timedelta = timedelta(seconds=11),
        namespace: str = "auth-tokens",
    ) -> None:
        """`store` pins one instead of the running context's; `fetch_wait_timeout` is how long a
        caller waits for the one fetching — at least the token endpoint's timeout."""
        self._init_key_value_store(store, namespace)
        self.now = now
        self.refetch_grace = refetch_grace
        self.namespace = namespace
        self._codec = SealedJsonCodec(TokenResponse, "issued-token-1", sealer)
        self._policy: CachePolicy[TokenResponse] = CachePolicy(
            freshness=TimeToLive(ttl=timedelta(hours=1)),
            validation=TokenValidity(now, skew),
            wait_for_others=fetch_wait_timeout,
        )
        self._caches: dict[int, Cache] = {}
        self._backoffs: dict[str, tuple[datetime, exceptions.EndpointUnavailable]] = {}
        """By the grant's digest: until when the endpoint asked not to be asked, and its
        refusal — dropped once that moment passed."""
        self._lock = threading.Lock()

    def _cache(self) -> Cache:
        """The `Cache` on the store given, else on the running context's — one per store."""
        store = self.store()
        with self._lock:
            if id(store) not in self._caches:
                self._caches[id(store)] = Cache(store, namespace=self.namespace, now=self.now)
            return self._caches[id(store)]

    def token(
        self, key: tuple[Any, ...], fetch: Callable[[], TokenResponse]
    ) -> TokenResponse:
        """The token cached under `key`, fetched by `fetch` when there is none still valid.

        1. The endpoint asked to wait and the wait has not passed: its refusal, again.
        2. Answered by the cache — one caller fetches, the others wait for it.
           2.1 The endpoint is unavailable with a Retry-After: stored until then — every wait
               already over dropped — and raised.
        Final: the token.
        """
        hashed = digest(json.dumps(key, default=str))
        with self._lock:
            backoff = self._backoffs.get(hashed)
            if backoff is not None and self.now() >= backoff[0]:
                del self._backoffs[hashed]
            elif backoff is not None:
                raise backoff[1]
        try:
            return self._cache().get_or_compute(key, fetch, self._policy, self._codec)
        except exceptions.EndpointUnavailable as refused:
            if refused.retry_after:
                now = self.now()
                with self._lock:
                    self._backoffs = {
                        one: wait for one, wait in self._backoffs.items() if wait[0] > now
                    }
                    self._backoffs[hashed] = (now + refused.retry_after, refused)
            raise

    def invalidate(
        self, key: tuple[Any, ...], token: str, fetch: Callable[[], TokenResponse]
    ) -> bool:
        """`token` was refused by the API: whether asking again may bring another.

        1. The cache holds another token now — fetched since by any replica: `True`.
        2. Younger than `refetch_grace`: a fresh token refused is a configuration to fix —
           `False`.
        Final: dropped from the cache, for every replica — `True`.
        """
        cached = self.token(key, fetch)
        if cached.access_token.get_secret_value() != token:
            return True
        if self.now() - cached.received_at < self.refetch_grace:
            return False
        self._cache().forget(key, self._codec)
        return True
