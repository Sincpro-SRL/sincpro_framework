"""The public keys of an issuer published as a JWKS and fetched (`Jwks`) — the `[jwt]` extra.

    Jwks("https://auth.sincpro.com.bo/realms/sincpro/protocol/openid-connect/certs")
    Jwks.from_discovery("https://auth.sincpro.com.bo/realms/sincpro")   the URL by OIDC discovery

Context: the set is fetched once and cached (`refresh_every`, ten minutes); an identity provider
that rotates its keys publishes the new one before it signs with it, so a token naming a `kid`
not in the set refetches it — once per `unknown_kid_cooldown`, so a stream of forged `kid`s
never turns into a stream of fetches. While the provider is down, the last good set is served
for `serve_stale_for` (a day); with nothing cached, `EndpointUnavailable` (503). A key whose
`use` is not `sig`, or that holds a private part, is left out.
"""

import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import timedelta
from typing import Any

from sincpro_framework.auth.common.adapters.jose_keys import PublicKey, is_compatible
from sincpro_framework.auth.common.adapters.public_keys import asymmetric_algorithms
from sincpro_framework.auth.common.adapters.server_metadata import (
    OidcDiscovery,
    resolve_discovery,
)
from sincpro_framework.auth.common.adapters.urllib_http import UrllibHttp
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.http import HttpClient, HttpRequest, redact_url
from sincpro_framework.auth.common.domain.keys import KeySource, VerificationKey
from sincpro_framework.data_layer.caching import (
    Cache,
    CachePolicy,
    FailSafe,
    JsonCodec,
    TimeToLive,
)
from sincpro_framework.exceptions import ServiceUnavailableError
from sincpro_framework.sincpro_logger import logger

DEFAULT_ALGORITHMS = ("RS256", "PS256", "ES256", "EdDSA")


def _select_key(
    keys: Sequence[Mapping[str, Any]], key_id: str | None, algorithm: str
) -> Mapping[str, Any] | None:
    """The one JWK for `key_id` and `algorithm` — by its `kid`, or the only one fitting the
    algorithm when the token names none."""
    fitting = [one for one in keys if is_compatible(one, algorithm)]
    if key_id is not None:
        return next((one for one in fitting if one.get("kid") == key_id), None)
    return fitting[0] if len(fitting) == 1 else None


class Jwks(KeySource):
    """An issuer's published key set, fetched, cached, refetched for a key it does not hold."""

    symmetric = False

    def __init__(
        self,
        url: str | Callable[[], str],
        algorithms: Iterable[str] = DEFAULT_ALGORITHMS,
        http: HttpClient | None = None,
        cache: Cache | None = None,
        refresh_every: timedelta = timedelta(minutes=10),
        unknown_kid_cooldown: timedelta = timedelta(seconds=30),
        serve_stale_for: timedelta = timedelta(hours=24),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`url` is the JWKS's — or a function answering it, asked on the first token
        (discovery); nothing is fetched here."""
        self._url = url
        self.algorithms = asymmetric_algorithms(algorithms)
        self.http: HttpClient = http or UrllibHttp()
        self.cache = cache or Cache(namespace="auth-jwks")
        self.unknown_kid_cooldown = unknown_kid_cooldown
        self.clock = clock
        self._policy: CachePolicy[dict[str, Any]] = CachePolicy(
            freshness=TimeToLive(ttl=refresh_every),
            failure=FailSafe(serve_for=serve_stale_for, errors=ServiceUnavailableError),
        )
        self._codec = JsonCodec(dict[str, Any])
        self._parsed: dict[tuple[str, str], PublicKey] = {}
        self._refetched_at = float("-inf")
        self._lock = threading.Lock()

    @classmethod
    def from_discovery(
        cls,
        issuer: str,
        algorithms: Iterable[str] = DEFAULT_ALGORITHMS,
        http: HttpClient | None = None,
        discovery: OidcDiscovery | None = None,
        **options: Any,
    ) -> "Jwks":
        """The JWKS whose URL `issuer`'s discovery document publishes (`jwks_uri`), found on the
        first token."""
        found = resolve_discovery(http, discovery)
        return cls(
            lambda: found.fetch(issuer).endpoint("jwks_uri"), algorithms, http=http, **options
        )

    @property
    def url(self) -> str:
        """The JWKS URL — discovered on the first call when it was given as a function."""
        return self._url() if callable(self._url) else self._url

    def _fetch(self, url: str) -> dict[str, Any]:
        """The key set as published — its signing keys only.

        1. Fetched; a status that is not success, or a body without a `keys` list:
           `EndpointUnavailable`.
        Final: the keys whose `use` is absent or `sig`, holding no private part.
        """
        answer = self.http.send(HttpRequest(url=url, headers={"accept": "application/json"}))
        if not answer.ok:
            raise exceptions.EndpointUnavailable(
                redact_url(url),
                f"answered {answer.status}",
                answer.status,
                answer.retry_after(),
            )
        keys = answer.json_object().get("keys")
        if not isinstance(keys, list):
            raise exceptions.EndpointUnavailable(redact_url(url), "answered no key set")
        published = [one for one in keys if isinstance(one, dict)]
        leaked = [one.get("kid") for one in published if "d" in one]
        if leaked:
            logger.error(
                "a JWKS publishes private keys — left out", url=redact_url(url), kids=leaked
            )
        return {
            "keys": [
                one for one in published if one.get("use", "sig") == "sig" and "d" not in one
            ]
        }

    def _keys(self, url: str) -> list[Mapping[str, Any]]:
        """The cached key set — fetched when it is not."""
        document = self.cache.get_or_compute(
            ("jwks", url), lambda: self._fetch(url), self._policy, self._codec
        )
        return list(document["keys"])

    def _refetch(self, url: str) -> list[Mapping[str, Any]] | None:
        """The key set fetched again now — `None` while the cooldown since the last refetch
        has not passed, so forged key ids never become a stream of fetches, and `None` when the
        issuer does not answer.

        Context: the cached set is replaced only by one fetched — an unknown key id while the
        issuer is down must not throw away the keys every other token is still verified with.
        """
        with self._lock:
            if self.clock() - self._refetched_at < self.unknown_kid_cooldown.total_seconds():
                return None
            self._refetched_at = self.clock()
        try:
            fetched = self._fetch(url)
        except ServiceUnavailableError as error:
            logger.warning(
                "the JWKS could not be fetched again", url=redact_url(url), error=str(error)
            )
            return None
        self.cache.forget(("jwks", url), self._codec)
        document = self.cache.get_or_compute(
            ("jwks", url), lambda: fetched, self._policy, self._codec
        )
        return list(document["keys"])

    def _key(self, jwk: Mapping[str, Any], algorithm: str) -> PublicKey:
        """The parsed key of `jwk` for `algorithm`, parsed once."""
        token = (str(sorted(jwk.items())), algorithm)
        if token not in self._parsed:
            self._parsed[token] = PublicKey.from_jwk(jwk, algorithm)
        return self._parsed[token]

    def key_for(self, key_id: str | None, algorithm: str) -> VerificationKey:
        """The published key for `key_id` and `algorithm`.

        1. An algorithm not accepted: `Unauthenticated`.
        2. Looked up in the cached set.
        3. Not there: the set refetched once — unless a refetch ran within the cooldown.
        Final: the key, or `Unauthenticated("…does not publish")`.
        """
        if algorithm not in self.algorithms:
            raise exceptions.Unauthenticated(f"token signed with {algorithm}, not accepted")
        url = self.url
        found = _select_key(self._keys(url), key_id, algorithm)
        if found is None:
            again = self._refetch(url)
            found = None if again is None else _select_key(again, key_id, algorithm)
        if found is None:
            raise exceptions.Unauthenticated(
                "token signed with a key the issuer does not publish"
            )
        return self._key(found, algorithm)
