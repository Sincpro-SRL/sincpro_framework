"""`FakeTokenEndpoint`: an authorization server in a test — no network — that issues tokens the
way a real one does, counts what it was asked, and fails when told to.

    server = FakeTokenEndpoint("https://auth.bank.bo", clients={"billing": "s3cret"})
    bank = ClientCredentials(client_id="billing", client_secret="s3cret",
                             issuer=server.issuer, http=server)
    server.grants("client_credentials")                      ->  how many tokens were fetched
    server.fail_next(503, retry_after=30)                     the next grant is refused
    api = server.resource()                                   a protected API, as `send`

Context: it answers discovery, `client_credentials`, `refresh_token` (rotating, a reused refresh
token revoking its family), RFC 8693 token exchange and RFC 8628 device authorization; tokens are
`at-1`, `at-2`… so a test reads which one it was given. Clients authenticate with
`client_secret_basic` or `client_secret_post`; a client assertion is accepted as it comes.
"""

import base64
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import parse_qsl, unquote

from sincpro_framework.auth.domain.http import HttpAnswer, HttpRequest
from sincpro_framework.runtime.testing.doubles.http import ScriptedHttp

TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
DEVICE_CODE = "urn:ietf:params:oauth:grant-type:device_code"


class FakeResource:
    """A protected API: 200 for a token the server issued and did not revoke, else 401."""

    def __init__(self, server: "FakeTokenEndpoint") -> None:
        self.server = server
        self.calls: list[dict[str, str]] = []

    def __call__(self, headers: Mapping[str, str]) -> HttpAnswer:
        self.calls.append(dict(headers))
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if scheme.lower() in ("bearer", "dpop") and self.server.accepts(token):
            return ScriptedHttp.answer({"ok": True, "token": token})
        return HttpAnswer(
            status=401, headers={"www-authenticate": 'Bearer error="invalid_token"'}
        )


class FakeTokenEndpoint(ScriptedHttp):
    """An `HttpClient` that is an authorization server."""

    def __init__(
        self,
        issuer: str = "https://auth.example",
        clients: Mapping[str, str] | None = None,
        expires_in: int | None = 300,
        delay: float = 0.0,
    ) -> None:
        super().__init__()
        self.issuer = issuer
        self.token_endpoint = f"{issuer}/token"
        self.device_endpoint = f"{issuer}/device"
        self.clients = dict(clients or {"client": "secret"})
        self.expires_in = expires_in
        self.delay = delay
        self.issued: list[str] = []
        self.revoked: set[str] = set()
        self.refresh: dict[str, dict[str, Any]] = {}
        self.devices: dict[str, dict[str, Any]] = {}
        self._failures: list[HttpAnswer] = []
        self._counter = 0
        self._grants: list[str] = []
        self._server_lock = threading.Lock()
        self.route(f"{issuer}/.well-known/openid-configuration", self._discovery)
        self.route(f"POST {self.token_endpoint}", self._token)
        self.route(f"POST {self.device_endpoint}", self._device)

    def _discovery(self, request: HttpRequest) -> HttpAnswer:
        return self.answer(
            {
                "issuer": self.issuer,
                "token_endpoint": self.token_endpoint,
                "device_authorization_endpoint": self.device_endpoint,
                "jwks_uri": f"{self.issuer}/certs",
            }
        )

    def fail_next(
        self,
        status: int,
        error: str = "temporarily_unavailable",
        retry_after: int | None = None,
    ) -> None:
        """The next grant answers `status` with `error`, and `Retry-After` when given."""
        headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
        self._failures.append(self.answer({"error": error}, status=status, headers=headers))

    def grants(self, kind: str | None = None) -> int:
        """How many grants were answered — of `kind` when named."""
        return sum(1 for one in self._grants if kind is None or one == kind)

    def accepts(self, token: str) -> bool:
        """Whether `token` is one this server issued and did not revoke."""
        return token in self.issued and token not in self.revoked

    def resource(self) -> FakeResource:
        """A protected API that accepts this server's tokens."""
        return FakeResource(self)

    def approve(self, user_code: str, subject: str = "user:device") -> None:
        """The person approves the device whose code is `user_code`."""
        for device in self.devices.values():
            if device["user_code"] == user_code:
                device["approved"] = subject

    def _next(self, prefix: str) -> str:
        with self._server_lock:
            self._counter += 1
            return f"{prefix}-{self._counter}"

    def _client_of(self, request: HttpRequest, form: dict[str, str]) -> str | None:
        """The client the request authenticates as — `None` when it does not."""
        scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "basic":
            client, _, secret = base64.b64decode(encoded).decode().partition(":")
            client, secret = unquote(client), unquote(secret)
            return client if self.clients.get(client) == secret else None
        if "client_assertion" in form:
            return form.get("client_id")
        client = form.get("client_id", "")
        if client in self.clients and form.get("client_secret") == self.clients[client]:
            return client
        return client if client in self.clients and self.clients[client] == "" else None

    def _issued(self, refresh_family: str | None = None) -> dict[str, Any]:
        """A new access token — with a rotated refresh token of `refresh_family` when given."""
        token = self._next("at")
        self.issued.append(token)
        said: dict[str, Any] = {"access_token": token, "token_type": "Bearer"}
        if self.expires_in is not None:
            said["expires_in"] = self.expires_in
        if refresh_family is not None:
            refresh = self._next("rt")
            self.refresh[refresh] = {"family": refresh_family, "used": False}
            said["refresh_token"] = refresh
        return said

    def _token(self, request: HttpRequest) -> HttpAnswer:
        """The token endpoint."""
        if self.delay:
            time.sleep(self.delay)
        with self._server_lock:
            if self._failures:
                return self._failures.pop(0)
        form = dict(parse_qsl((request.body or b"").decode()))
        grant = form.get("grant_type", "")
        client = self._client_of(request, form)
        if client is None:
            return self.answer({"error": "invalid_client"}, status=401)
        with self._server_lock:
            self._grants.append(grant)
        handlers: dict[str, Callable[[dict[str, str]], HttpAnswer]] = {
            "client_credentials": lambda _: self.answer(self._issued()),
            "refresh_token": self._refreshed,
            TOKEN_EXCHANGE: self._exchanged,
            DEVICE_CODE: self._device_token,
            "password": lambda _: self.answer(self._issued(self._next("family"))),
        }
        handler = handlers.get(grant)
        if handler is None:
            return self.answer({"error": "unsupported_grant_type"}, status=400)
        return handler(form)

    def _refreshed(self, form: dict[str, str]) -> HttpAnswer:
        """A refresh: rotated; a refresh token used twice revokes its whole family."""
        with self._server_lock:
            held = self.refresh.get(form.get("refresh_token", ""))
            if held is None:
                return self.answer({"error": "invalid_grant"}, status=400)
            if held["used"]:
                for one in self.refresh.values():
                    if one["family"] == held["family"]:
                        one["used"] = True
                return self.answer(
                    {"error": "invalid_grant", "error_description": "reused"}, status=400
                )
            held["used"] = True
        return self.answer(self._issued(held["family"]))

    def _exchanged(self, form: dict[str, str]) -> HttpAnswer:
        """RFC 8693: a token for `audience` acting for the subject token."""
        if not form.get("subject_token"):
            return self.answer({"error": "invalid_request"}, status=400)
        if form["subject_token"] in self.revoked:
            return self.answer({"error": "invalid_grant"}, status=400)
        said = self._issued()
        said["issued_token_type"] = "urn:ietf:params:oauth:token-type:access_token"
        return self.answer(said)

    def _device(self, request: HttpRequest) -> HttpAnswer:
        """RFC 8628 device authorization."""
        device_code, user_code = self._next("dc"), self._next("UC").upper()
        self.devices[device_code] = {"user_code": user_code, "approved": None, "polls": 0}
        return self.answer(
            {
                "device_code": device_code,
                "user_code": user_code,
                "verification_uri": f"{self.issuer}/activate",
                "expires_in": 600,
                "interval": 1,
            }
        )

    def _device_token(self, form: dict[str, str]) -> HttpAnswer:
        device = self.devices.get(form.get("device_code", ""))
        if device is None:
            return self.answer({"error": "expired_token"}, status=400)
        device["polls"] += 1
        if device["approved"] is None:
            return self.answer({"error": "authorization_pending"}, status=400)
        return self.answer(self._issued(self._next("family")))
