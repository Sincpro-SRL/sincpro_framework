"""`DeviceCode`: a CLI or a device with no browser gets a person's consent — RFC 8628 — by
showing a code to enter on another screen, and polling until it was.

    issued = DeviceCode(PublicClient("sincpro-cli"), issuer=KEYCLOAK, scope=["openid"],
                        show=lambda prompt: print(prompt.verification_uri, prompt.user_code)
                        ).authorize()
    RefreshingToken(..., key="cli").seed(issued)          held, refreshed from now on

Context: `authorization_pending` polls again after `interval`; `slow_down` adds five seconds to
it; a person who refused, or a code that expired, is `Unauthenticated` — authorize again.
"""

import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from functools import partial

from pydantic import ConfigDict

from sincpro_framework.auth.client.infrastructure.token_endpoint import (
    IssuedToken,
    TokenEndpoint,
)
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.http import ClientAuthentication, HttpClient, HttpRequest
from sincpro_framework.auth.infrastructure.server_metadata import (
    OidcDiscovery,
    discovery_for,
    located,
)
from sincpro_framework.auth.infrastructure.urllib_http import UrllibHttp
from sincpro_framework.sincpro_abstractions import DataTransferObject

DEVICE_CODE = "urn:ietf:params:oauth:grant-type:device_code"


class DevicePrompt(DataTransferObject):
    """What the person is shown: where to go and the code to enter."""

    model_config = ConfigDict(frozen=True)

    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_in: int = 600


class DeviceCode:
    """One device authorization, shown with `show` and polled until it is answered."""

    def __init__(
        self,
        client: ClientAuthentication,
        show: Callable[[DevicePrompt], None],
        issuer: str | None = None,
        device_endpoint: str | None = None,
        token_endpoint: str | None = None,
        scope: Iterable[str] = (),
        http: HttpClient | None = None,
        discovery: OidcDiscovery | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        self.client = client
        self.show = show
        self.discovery = discovery_for(http, discovery)
        self.http: HttpClient = http or UrllibHttp()
        self._device = located(
            device_endpoint, issuer, self.discovery, "device_authorization_endpoint"
        )
        self.issuer = issuer
        self.endpoint = TokenEndpoint(
            located(token_endpoint, issuer, self.discovery, "token_endpoint"),
            client,
            self.http,
            now,
        )
        self.scope = tuple(sorted(scope))
        self.sleep = sleep

    def _device_endpoint(self) -> str:
        """The device authorization endpoint — given, or discovered on the first call."""
        return self._device() if callable(self._device) else self._device

    def authorize(self) -> IssuedToken:
        """The token, once the person consented.

        1. The device authorization asked; its prompt shown.
        2. Polled every `interval`: pending polls again, `slow_down` waits five seconds more.
        3. Refused or expired: `Unauthenticated`.
        Final: the token issued.
        """
        url = self._device_endpoint()
        fields = [("scope", " ".join(self.scope))] if self.scope else []
        sent, headers = self.client.applied(fields, url)
        answer = self.http.send(HttpRequest.form(url, sent, headers))
        said = answer.json_object()
        if not answer.ok or "device_code" not in said:
            raise exceptions.CredentialsRejected(
                HttpRequest(url=url).target, str(said.get("error", answer.status))
            )
        self.show(
            DevicePrompt(
                user_code=said["user_code"],
                verification_uri=said["verification_uri"],
                verification_uri_complete=said.get("verification_uri_complete"),
                expires_in=int(said.get("expires_in", 600)),
            )
        )
        interval = float(said.get("interval", 5))
        while True:
            try:
                return self.endpoint.grant(
                    [("grant_type", DEVICE_CODE), ("device_code", said["device_code"])]
                )
            except exceptions.CredentialsRejected as refused:
                if refused.error == "slow_down":
                    interval += 5
                elif refused.error != "authorization_pending":
                    raise exceptions.Unauthenticated(
                        f"device authorization {refused.error}"
                    ) from refused
            self.sleep(interval)
