"""`DeviceAuthorization`: a CLI or a device with no browser gets a person's consent — RFC 8628 — by
showing a code to enter on another screen, and polling until it was.

    issued = DeviceAuthorization(
        lambda prompt: print(prompt.verification_uri, prompt.user_code),
        client_id="sincpro-cli", issuer=KEYCLOAK, scope=["openid"],     PublicClient
    ).authorize()
    RefreshTokenSource("cli", ...).store_grant(issued)          stored, refreshed from now on

Context: `authorization_pending` polls again after `interval`; `slow_down` adds five seconds to it
(RFC 8628 §3.5); past the code's `expires_in` — by this service's clock, or said by the server
(`expired_token`) — it is `GrantRejected`: authorize again. A person who refused (`access_denied`)
is `Unauthenticated`; any other refusal is this service's client: `CredentialsRejected`.
"""

import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict

from sincpro_framework.auth.client.adapters.token_endpoint import TokenEndpointMixin
from sincpro_framework.auth.client.domain.token_response import TokenResponse, parse_seconds
from sincpro_framework.auth.common.adapters.server_metadata import (
    OidcDiscovery,
    resolve_endpoint,
)
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.http import (
    ClientAuthentication,
    HttpClient,
    HttpRequest,
)
from sincpro_framework.sincpro_abstractions import DataTransferObject

if TYPE_CHECKING:
    from sincpro_framework.auth.client.adapters.dpop_prover import DpopProver

DEVICE_CODE = "urn:ietf:params:oauth:grant-type:device_code"
SLOW_DOWN_STEP = timedelta(seconds=5)
"""What `slow_down` adds to the polling interval — RFC 8628 §3.5."""


class DeviceAuthorizationPrompt(DataTransferObject):
    """What the person is shown: where to go and the code to enter."""

    model_config = ConfigDict(frozen=True)

    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_in: int = 600


class DeviceAuthorization(TokenEndpointMixin):
    """One device authorization, shown with `on_prompt` and polled until it is answered."""

    def __init__(
        self,
        on_prompt: Callable[[DeviceAuthorizationPrompt], None],
        client: ClientAuthentication | None = None,
        client_id: str | None = None,
        client_secret: Any = None,
        issuer: str | None = None,
        device_endpoint: str | None = None,
        token_endpoint: str | None = None,
        scope: Iterable[str] = (),
        http: HttpClient | None = None,
        discovery: OidcDiscovery | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
        timeout: timedelta = timedelta(seconds=10),
        dpop: "DpopProver | None" = None,
    ) -> None:
        """`on_prompt` shows the person the code; `client_id` alone is a `PublicClient` — what a
        CLI is."""
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
            (),
            dpop,
        )
        self.on_prompt = on_prompt
        self._device = resolve_endpoint(
            device_endpoint, issuer, self.discovery, "device_authorization_endpoint"
        )
        self.issuer = issuer
        self.now = now
        self.sleep = sleep

    def _device_endpoint(self) -> str:
        """The device authorization endpoint — given, or discovered on the first call."""
        return self._device() if callable(self._device) else self._device

    def _request_device_code(self) -> tuple[str, DeviceAuthorizationPrompt, float]:
        """The device code, the prompt to show and the polling interval in seconds — an answer
        without a device code, a user code or a verification URI is `CredentialsRejected`."""
        url = self._device_endpoint()
        fields = [("scope", " ".join(self.scope))] if self.scope else []
        sent, headers = self.client.apply(fields, url)
        answer = self.http.send(HttpRequest.form(url, sent, headers, self.endpoint.timeout))
        said = answer.json_object()
        target = HttpRequest(url=url).target
        if not answer.ok:
            raise exceptions.CredentialsRejected(
                target,
                str(said.get("error") or f"status {answer.status}"),
                str(said.get("error_description", "")),
            )
        needed = ("device_code", "user_code", "verification_uri")
        if not all(isinstance(said.get(name), str) and said[name] for name in needed):
            raise exceptions.CredentialsRejected(
                target, "malformed_answer", "no device_code, user_code or verification_uri"
            )
        uri_complete = said.get("verification_uri_complete")
        prompt = DeviceAuthorizationPrompt(
            user_code=said["user_code"],
            verification_uri=said["verification_uri"],
            verification_uri_complete=uri_complete if isinstance(uri_complete, str) else None,
            expires_in=int(parse_seconds(said.get("expires_in")) or 600),
        )
        return said["device_code"], prompt, parse_seconds(said.get("interval")) or 5.0

    def authorize(self) -> TokenResponse:
        """The token, once the person consented.

        1. The device authorization asked; its prompt shown; the deadline `expires_in` from
           now.
        2. Polled every `interval`: pending polls again, `slow_down` waits five seconds more.
        3. Past the deadline, or `expired_token`: `GrantRejected`; `access_denied`:
           `Unauthenticated`; any other refusal: as the endpoint raised it.
        Final: the token issued.
        """
        device_code, prompt, interval = self._request_device_code()
        deadline = self.now() + timedelta(seconds=prompt.expires_in)
        self.on_prompt(prompt)
        fields = [("grant_type", DEVICE_CODE), ("device_code", device_code)]
        while True:
            if self.now() >= deadline:
                raise exceptions.GrantRejected(
                    HttpRequest(url=self.endpoint.url).target,
                    "expired_token",
                    "the device code expired unanswered",
                )
            try:
                return self.endpoint.request_token(fields, dpop=self.dpop)
            except exceptions.CredentialsRejected as refused:
                if refused.error == "slow_down":
                    interval += SLOW_DOWN_STEP.total_seconds()
                elif refused.error == "expired_token":
                    raise exceptions.GrantRejected(
                        refused.endpoint, refused.error, refused.description
                    ) from refused
                elif refused.error == exceptions.OAuthErrorCode.ACCESS_DENIED:
                    raise exceptions.Unauthenticated(
                        "the person refused the device authorization"
                    ) from refused
                elif refused.error != "authorization_pending":
                    raise
            self.sleep(interval)
