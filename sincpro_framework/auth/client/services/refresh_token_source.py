"""`RefreshTokenSource`: an API this service holds a refresh token for — a tenant's Odoo consented
once, a CLI authorized by device code — refreshed when its access token runs out, by one
replica at a time.

    odoo = RefreshTokenSource("odoo:tenant-bo", KeyValueOAuthGrants(),
                              client_id="billing", client_secret=secret,
                              issuer="https://odoo.acme.bo")
    odoo.store_grant(issued)               the grant obtained once — DeviceAuthorization, a consent
    odoo.headers_for(request)              ->  {"authorization": "Bearer …"}, refreshed when due

Context: a server that rotates refresh tokens revokes the whole grant when one is used twice —
so a refresh runs under a lease (`Leases`): one replica refreshes, the others wait for its answer.
It is stored by compare-and-set on the grant's `version`, which counts refresh tokens, never the
access token's expiry: a 401 marking the access token expired (`invalidate`) while a refresh runs
never makes the refresh lose its rotated refresh token. A refresh that still loses — the lease
expired, another write landed — rewrites over a grant that holds the refresh token it spent, and
leaves one holding another (a new consent). A server that does not rotate leaves the old refresh
token in place. `invalid_grant` deletes the grant: consent must be given again (`GrantRejected`).
A refresh token deleted or overwritten here is not revoked at the server (RFC 7009).
"""

import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.client.adapters.oauth_grant_store import KeyValueOAuthGrants
from sincpro_framework.auth.client.adapters.token_endpoint import TokenEndpointMixin
from sincpro_framework.auth.client.domain.outbound import (
    OAuthGrant,
    OAuthGrantStore,
    OutgoingRequest,
    TokenSource,
    parse_bearer_token,
)
from sincpro_framework.auth.client.domain.token_response import TokenResponse
from sincpro_framework.auth.common.adapters.key_value import (
    MAX_COMPARE_AND_SET_ATTEMPTS,
    KeyValueStoreMixin,
)
from sincpro_framework.auth.common.adapters.server_metadata import OidcDiscovery
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.http import ClientAuthentication, HttpClient
from sincpro_framework.data_layer.store import KeyValueStore, Leases
from sincpro_framework.exceptions import ServiceUnavailableError
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.auth.client.adapters.dpop_prover import DpopProver


class RefreshTokenSource(KeyValueStoreMixin, TokenEndpointMixin, TokenSource):
    """The access token of a stored grant, refreshed under a lease and stored by
    compare-and-set."""

    warn_when_unshared = True

    def __init__(
        self,
        key: str,
        grants: OAuthGrantStore | None = None,
        client: ClientAuthentication | None = None,
        client_id: str | None = None,
        client_secret: Any = None,
        token_endpoint: str | None = None,
        issuer: str | None = None,
        scope: Iterable[str] = (),
        resources: Iterable[str] = (),
        http: HttpClient | None = None,
        discovery: OidcDiscovery | None = None,
        store: KeyValueStore | None = None,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
        skew: timedelta = timedelta(seconds=30),
        lease_timeout: timedelta = timedelta(seconds=10),
        timeout: timedelta = timedelta(seconds=10),
        sleep: Callable[[float], None] = time.sleep,
        name: str = "refresh-token",
        dpop: "DpopProver | None" = None,
    ) -> None:
        """`key` names the grant in `grants` — one per API and tenant; `grants` is a
        `KeyValueOAuthGrants` on the running context's store when not given; `store` holds the
        lease, the running context's when not given; `lease_timeout` is how long a replica waits
        for another's refresh before it refreshes itself."""
        self._init_token_endpoint(
            client,
            client_id,
            client_secret,
            token_endpoint,
            issuer,
            http,
            discovery,
            now,
            timeout,
            scope,
            resources,
            dpop,
        )
        self._init_key_value_store(store, "sincpro:auth:refreshing")
        self.grants = grants if grants is not None else KeyValueOAuthGrants()
        self.key = key
        self.now = now
        self.skew = skew
        self.lease_timeout = lease_timeout
        self.sleep = sleep
        self.name = name

    def store_grant(self, issued: TokenResponse) -> None:
        """Store the grant `issued` — the first one, from a consent or a device code — over any
        stored before, its `version` one more. A token with no refresh token cannot be stored:
        `ProgrammingError`."""
        if issued.refresh_token is None:
            raise exceptions.ProgrammingError("the token response holds no refresh token")
        refresh_token = issued.refresh_token.get_secret_value()
        for _ in range(MAX_COMPARE_AND_SET_ATTEMPTS):
            stored = self.grants.get(self.key)
            expected = None if stored is None else stored.version
            grant = OAuthGrant.build(
                issued, refresh_token, 0 if stored is None else stored.version + 1
            )
            if self.grants.compare_and_set(self.key, expected, grant):
                return
        raise ServiceUnavailableError(f"the grant {self.key} is contended: try again")

    def _store_refreshed(self, spent: OAuthGrant, refreshed: OAuthGrant) -> None:
        """`refreshed` stored over `spent` by compare-and-set on the version.

        1. Lost: the stored grant read again.
           1.1 It still holds the refresh token `spent` — marked expired, written again —
               `refreshed` is written over it, one version above.
           1.2 It holds another refresh token, or none: left as it is — a new consent, or a
               grant deleted — and the rotated refresh token dropped, warned about.
        Final: stored.
        """
        expected = spent.version
        for _ in range(MAX_COMPARE_AND_SET_ATTEMPTS):
            if self.grants.compare_and_set(self.key, expected, refreshed):
                return
            stored = self.grants.get(self.key)
            if stored is None or stored.refresh_token != spent.refresh_token:
                logger.warning(
                    "a rotated refresh token was not stored: the grant changed meanwhile",
                    key=self.key,
                )
                return
            expected = stored.version
            refreshed = refreshed.model_copy(update={"version": stored.version + 1})
        raise ServiceUnavailableError(f"the grant {self.key} is contended: try again")

    def _refresh(self, grant: OAuthGrant) -> OAuthGrant:
        """`grant` refreshed at the endpoint and stored by compare-and-set.

        1. `invalid_grant`: the grant deleted — unless another was stored meanwhile —
           `GrantRejected` raised.
        2. The answer with its rotated refresh token — the one spent when not rotated.
        Final: stored (`_store_refreshed`); the answer either way.
        """
        fields = [("grant_type", "refresh_token"), ("refresh_token", grant.refresh_token)]
        try:
            issued = self._request_token(fields)
        except exceptions.GrantRejected:
            self.grants.compare_and_set(self.key, grant.version, None)
            raise
        refresh_token = (
            issued.refresh_token.get_secret_value()
            if issued.refresh_token
            else grant.refresh_token
        )
        refreshed = OAuthGrant.build(issued, refresh_token, grant.version + 1)
        self._store_refreshed(grant, refreshed)
        return refreshed

    def _wait_for_peer_refresh(self, stale: OAuthGrant) -> OAuthGrant | None:
        """The grant once another replica refreshed it — `None` past `lease_timeout`."""
        give_up = time.monotonic() + self.lease_timeout.total_seconds()
        while time.monotonic() < give_up:
            self.sleep(0.05)
            stored = self.grants.get(self.key)
            if (
                stored is not None
                and stored.version != stale.version
                and stored.is_valid(self.now(), self.skew)
            ):
                return stored
        return None

    def _valid_grant(self) -> OAuthGrant:
        """A grant whose access token is valid.

        1. None stored: `ProgrammingError` — `store_grant` it first.
        2. Valid: as it is.
        3. The lease held by another replica: its refresh awaited for `lease_timeout`; still
           running, the lease is asked once more — and refused, `EndpointUnavailable` (503):
           refreshing without it would spend a refresh token the other may be spending, and a
           rotating server revokes the whole grant on that reuse.
        Final: under the lease, refreshed — unless another replica already did.
        """
        stored = self.grants.get(self.key)
        if stored is None:
            raise exceptions.ProgrammingError(
                f"no grant is stored for {self.key}: call store_grant first"
            )
        if stored.is_valid(self.now(), self.skew):
            return stored
        leases = Leases(self.store(), prefix=self.prefix)
        lease = leases.take(self.key, ttl=self.lease_timeout * 2)
        if lease is None:
            refreshed = self._wait_for_peer_refresh(stored)
            if refreshed is not None:
                return refreshed
            lease = leases.take(self.key, ttl=self.lease_timeout * 2)
            if lease is None:
                raise exceptions.EndpointUnavailable(
                    f"refresh of {self.key}",
                    "another replica's refresh is still running",
                    retry_after=self.lease_timeout,
                )
        try:
            again = self.grants.get(self.key)
            if again is not None and again.is_valid(self.now(), self.skew):
                return again
            return self._refresh(again or stored)
        finally:
            lease.release()

    def headers_for(self, request: OutgoingRequest) -> dict[str, str]:
        """`Bearer` — or `DPoP` with this request's proof — with the stored access token,
        refreshed first, by one caller across replicas, when it is about to expire."""
        grant = self._valid_grant()
        return self._authorization_headers(
            request, grant.access_token or "", grant.token_type
        )

    def invalidate(
        self,
        request: OutgoingRequest,
        presented: Mapping[str, str],
        answer: Mapping[str, str] | None = None,
    ) -> bool:
        """The API refused the access token: a new DPoP nonce stored is worth asking again;
        else the stored access token marked expired — same version, so a refresh running now
        still stores its rotated refresh token — and the next call refreshes."""
        if self.dpop is not None and answer and self.dpop.update_nonce(request.url, answer):
            return True
        token = parse_bearer_token(presented)
        for _ in range(MAX_COMPARE_AND_SET_ATTEMPTS):
            stored = self.grants.get(self.key)
            if stored is None or token is None:
                return False
            if stored.access_token != token:
                return True
            expired = stored.model_copy(update={"expires_at": self.now()})
            if self.grants.compare_and_set(self.key, stored.version, expired):
                return True
        return True

    def describe(self) -> dict[str, Any]:
        """The key the grant is stored under, and the client refreshing it."""
        return {**super().describe(), "key": self.key, "client_id": self.client.client_id}
