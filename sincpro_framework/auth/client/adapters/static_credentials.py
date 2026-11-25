"""Credentials that do not change: a token, an API key, a username and password.

    StaticToken(settings.siat_token, header="apikey", scheme="TokenApi")   SIAT
    StaticToken(settings.deepseek_key)                                       Authorization: Bearer …
    ApiKeyHeader(settings.odoo_key, header="odoo-api-key")
    BasicCredentials("billing", settings.jmap_password)                     RFC 7617

Context: held as given — text or a pydantic `Secret` — and read when a request is made; no
`repr` shows it. A static credential refused is refused again: `invalidate` says so, and the
caller does not retry.
"""

import base64
from typing import Any

from sincpro_framework.auth.client.domain.outbound import OutgoingRequest, TokenSource
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.infrastructure.digests import reveal_secret


class StaticToken(TokenSource):
    """A fixed token in `header`, after `scheme` when there is one."""

    def __init__(
        self,
        token: Any,
        header: str = "authorization",
        scheme: str | None = "Bearer",
        name: str = "static-token",
    ) -> None:
        self._token = token
        self.header = header.lower()
        self.scheme = scheme
        self.name = name

    def headers_for(self, request: OutgoingRequest) -> dict[str, str]:
        """`<scheme> <token>` under `header` — the bare token when there is no scheme."""
        value = reveal_secret(self._token)
        return {self.header: f"{self.scheme} {value}" if self.scheme else value}

    def __repr__(self) -> str:
        return f"StaticToken(header={self.header!r}, scheme={self.scheme!r})"


class ApiKeyHeader(StaticToken):
    """A fixed API key in its own header — `x-api-key` by default."""

    def __init__(self, key: Any, header: str = "x-api-key", name: str = "api-key") -> None:
        super().__init__(key, header=header, scheme=None, name=name)

    def __repr__(self) -> str:
        return f"ApiKeyHeader(header={self.header!r})"


class BasicCredentials(TokenSource):
    """HTTP Basic (RFC 7617): a username and a password, base64 in `Authorization`."""

    def __init__(self, username: str, password: Any, name: str = "basic") -> None:
        """A username holding `:` cannot be sent: `ProgrammingError` here, not a 401 later."""
        if ":" in username:
            raise exceptions.ProgrammingError("a Basic username cannot hold ':'")
        self.username = username
        self._password = password
        self.name = name

    def headers_for(self, request: OutgoingRequest) -> dict[str, str]:
        """`Basic` with `username:password` in base64 (RFC 7617)."""
        pair = f"{self.username}:{reveal_secret(self._password)}".encode()
        return {"authorization": f"Basic {base64.b64encode(pair).decode()}"}

    def __repr__(self) -> str:
        return f"BasicCredentials(username={self.username!r})"
