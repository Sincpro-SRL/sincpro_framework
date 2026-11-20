"""`RefreshingToken`: an API this service holds a refresh token for — a tenant's Odoo consented
once, a CLI authorized by device code — refreshed when its access token runs out, by one
replica at a time.

    odoo = RefreshingToken(ClientSecretBasic("billing", secret), KeyValueHeldGrants(),
                           key="odoo:tenant-bo", issuer="https://odoo.acme.bo")
    odoo.seed(issued)                      the grant obtained once — DeviceCode, a consent screen
    odoo.headers_for(request)              ->  {"authorization": "Bearer …"}, refreshed when due

Context: a server that rotates refresh tokens revokes the whole grant when one is used twice —
so a refresh runs under a lease (`Leases`): one replica refreshes, the others wait for its
answer; it is stored by compare-and-set, and a replica that lost the race keeps the token it got
without overwriting the newer grant. A server that does not rotate keeps the old refresh token.
`invalid_grant` drops the grant: consent must be given again (`GrantRejected`).
"""

import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sincpro_framework.auth.adapters.outbound.client_credentials import endpoint_of
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.http import ClientAuthentication, HttpClient
from sincpro_framework.auth.domain.outbound import (
    HeldGrant,
    HeldGrants,
    OutgoingRequest,
    TokenSource,
)
from sincpro_framework.auth.infrastructure.urllib_http import UrllibHttp
from sincpro_framework.auth.services.server_metadata import OidcDiscovery
from sincpro_framework.auth.services.token_cache import bearer_of
from sincpro_framework.auth.services.token_endpoint import IssuedToken, TokenEndpoint
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore, Leases


class RefreshingToken(TokenSource):
    """The access token of a held grant, refreshed under a lease and stored by compare-and-set."""

    def __init__(
        self,
        client: ClientAuthentication,
        grants: HeldGrants,
        key: str,
        token_endpoint: str | None = None,
        issuer: str | None = None,
        scope: Iterable[str] = (),
        http: HttpClient | None = None,
        discovery: OidcDiscovery | None = None,
        store: KeyValueStore | None = None,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
        skew: timedelta = timedelta(seconds=30),
        wait: timedelta = timedelta(seconds=10),
        sleep: Callable[[float], None] = time.sleep,
        name: str = "refreshing-token",
    ) -> None:
        """`key` names the grant in `grants` — one per API and tenant; `store` holds the lease,
        the running context's when not given; `wait` is how long a replica waits for another's
        refresh before it refreshes itself."""
        self.client = client
        self.grants = grants
        self.key = key
        self.http: HttpClient = http or UrllibHttp()
        self.discovery = discovery or OidcDiscovery(http=self.http)
        self.endpoint = TokenEndpoint(
            endpoint_of(token_endpoint, issuer, self.discovery), client, self.http, now
        )
        self.scope = tuple(sorted(scope))
        self._store = store
        self.now = now
        self.skew = skew
        self.wait = wait
        self.sleep = sleep
        self.name = name

    def seed(self, issued: IssuedToken) -> None:
        """Hold the grant `issued` — the first one, from a consent or a device code. A token
        with no refresh token cannot be held: `ProgrammingError`."""
        if issued.refresh_token is None:
            raise exceptions.ProgrammingError("the token holds no refresh token to keep")
        grant = HeldGrant(
            refresh_token=issued.refresh_token.get_secret_value(),
            access_token=issued.access_token.get_secret_value(),
            expires_at=issued.expires_at,
        )
        current = self.grants.current(self.key)
        self.grants.replace(self.key, current, grant)

    def _good(self, grant: HeldGrant | None) -> bool:
        """Whether `grant`'s access token is still good — not past `expires_at - skew`."""
        return (
            grant is not None
            and grant.access_token is not None
            and grant.expires_at is not None
            and self.now() < grant.expires_at - self.skew
        )

    def _refreshed(self, grant: HeldGrant) -> HeldGrant:
        """`grant` refreshed at the endpoint and stored by compare-and-set.

        1. `invalid_grant`: the grant dropped, `GrantRejected` raised.
        2. The answer kept with its rotated refresh token — the old one when not rotated.
        Final: stored when nobody stored another meanwhile; the answer either way.
        """
        fields = [("grant_type", "refresh_token"), ("refresh_token", grant.refresh_token)]
        if self.scope:
            fields.append(("scope", " ".join(self.scope)))
        try:
            issued = self.endpoint.grant(fields)
        except exceptions.GrantRejected:
            self.grants.replace(self.key, grant, None)
            raise
        refreshed = HeldGrant(
            refresh_token=(
                issued.refresh_token.get_secret_value()
                if issued.refresh_token
                else grant.refresh_token
            ),
            access_token=issued.access_token.get_secret_value(),
            expires_at=issued.expires_at,
            version=grant.version + 1,
        )
        self.grants.replace(self.key, grant, refreshed)
        return refreshed

    def _awaited(self, held: HeldGrant) -> HeldGrant | None:
        """The grant once another replica refreshed it — `None` past `wait`."""
        give_up = time.monotonic() + self.wait.total_seconds()
        while time.monotonic() < give_up:
            self.sleep(0.05)
            now = self.grants.current(self.key)
            if now is not None and now.version != held.version and self._good(now):
                return now
        return None

    def _current(self) -> HeldGrant:
        """A grant whose access token is good.

        1. None held: `ProgrammingError` — seed it first.
        2. Good: as it is.
        3. Else the refresh lease taken: refreshed — unless another replica already did.
        Final: the lease held by another: its refresh awaited, else refreshed here.
        """
        held = self.grants.current(self.key)
        if held is None:
            raise exceptions.ProgrammingError(
                f"no grant is held for {self.key}: seed it first"
            )
        if self._good(held):
            return held
        store = (
            self._store if self._store is not None else key_value_store_of(current_store())
        )
        lease = Leases(store, prefix="sincpro:auth:refreshing").take(
            self.key, ttl=self.wait * 2
        )
        if lease is None:
            return self._awaited(held) or self._refreshed(held)
        try:
            again = self.grants.current(self.key)
            if again is not None and self._good(again):
                return again
            return self._refreshed(again or held)
        finally:
            lease.release()

    def headers_for(self, request: OutgoingRequest) -> dict[str, str]:
        return {"authorization": f"Bearer {self._current().access_token}"}

    def invalidate(
        self,
        request: OutgoingRequest,
        presented: Mapping[str, str],
        answer: Mapping[str, str] | None = None,
    ) -> bool:
        """The API refused the access token: held as expired, so the next call refreshes."""
        held = self.grants.current(self.key)
        token = bearer_of(presented)
        if held is None or token is None:
            return False
        if held.access_token != token:
            return True
        return self.grants.replace(
            self.key, held, held.model_copy(update={"expires_at": self.now()})
        )

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "key": self.key, "client_id": self.client.client_id}
