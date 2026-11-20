"""The one HTTP port auth speaks through — to an identity provider's discovery, keys, token and
introspection endpoints, to a policy engine — and how a client authenticates to them.

    http.send(HttpRequest.form("https://auth.acme.bo/token", [("grant_type", "client_credentials")]))
        ->  HttpAnswer(status=200, headers={"content-type": "application/json"}, body=b"{…}")

Context: an answer of any status comes back as an answer — OAuth tells why it refused in the
body of a 400 — and only a request nothing answered raises `EndpointUnavailable`. The port is a
`Protocol` so a test hands a scripted one (`runtime.testing.ScriptedHttp`) and a project may hand
httpx; the framework's own is `UrllibHttp`, the standard library.
"""

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

from pydantic import ConfigDict, Field, field_validator

from sincpro_framework.sincpro_abstractions import DataTransferObject

MAX_RETRY_AFTER = timedelta(minutes=5)
"""The longest wait an endpoint's `Retry-After` is taken for — a misconfigured endpoint never
parks a client for a day."""


class HttpRequest(DataTransferObject):
    """What is sent: method, URL, headers, body and how long to wait for the whole answer."""

    model_config = ConfigDict(frozen=True)

    url: str
    method: str = "GET"
    headers: Mapping[str, str] = Field(default_factory=dict)
    body: bytes | None = None
    timeout: timedelta = timedelta(seconds=10)

    @classmethod
    def form(
        cls,
        url: str,
        fields: Sequence[tuple[str, str]],
        headers: Mapping[str, str] | None = None,
        timeout: timedelta = timedelta(seconds=10),
    ) -> "HttpRequest":
        """A POST of `fields` form-encoded, asking for JSON back — what every OAuth endpoint
        takes. A name may repeat (`resource=`, RFC 8707).

            HttpRequest.form("https://h/token", [("grant_type", "client_credentials")])
                ->  POST https://h/token, body b"grant_type=client_credentials"
        """
        return cls(
            url=url,
            method="POST",
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "accept": "application/json",
                **(headers or {}),
            },
            body=urlencode(list(fields)).encode(),
            timeout=timeout,
        )

    @property
    def target(self) -> str:
        """The URL without userinfo, query or fragment — what a log line or an error may say.

        HttpRequest(url="https://u:p@h/token?x=1#f").target   ->  "https://h/token"
        """
        return safe_url(self.url)

    def __repr__(self) -> str:
        """The method, the target and the header names — never a header's value, never the body.

        Context: a request to a token endpoint carries a client secret in a header or the body.
        """
        return f"HttpRequest({self.method} {self.target}, headers={sorted(self.headers)})"

    __str__ = __repr__


def safe_url(url: str) -> str:
    """`url` without userinfo, query or fragment.

    safe_url("https://client:secret@auth.acme.bo/token?code=x#y")  ->  "https://auth.acme.bo/token"
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


class HttpAnswer(DataTransferObject):
    """What came back: a status, the headers with lower-case names, the body."""

    model_config = ConfigDict(frozen=True)

    status: int
    headers: Mapping[str, str] = Field(default_factory=dict)
    body: bytes = b""

    @field_validator("headers")
    @classmethod
    def _lower_case(cls, headers: Mapping[str, str]) -> Mapping[str, str]:
        """Header names lower-cased, as `Credentials` keeps them."""
        return {name.lower(): value for name, value in headers.items()}

    @property
    def ok(self) -> bool:
        """Whether the status is a success (2xx)."""
        return 200 <= self.status < 300

    def parsed(self) -> Any:
        """The body parsed as JSON — `ValueError` when it is not."""
        return json.loads(self.body or b"null")

    def json_object(self) -> dict[str, Any]:
        """The body as a JSON object; an empty dict when it is anything else — an error body
        a server wrote as text says nothing an OAuth client reads."""
        try:
            value = self.parsed()
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def retry_after(self, now: datetime | None = None) -> timedelta | None:
        """The wait `Retry-After` asks — seconds or an HTTP date — capped at five minutes;
        `None` when it asks none or cannot be read.

            Retry-After: 30                              ->  timedelta(seconds=30)
            Retry-After: Wed, 21 Oct 2026 07:28:00 GMT   ->  the time until then
        """
        said = self.headers.get("retry-after", "").strip()
        if not said:
            return None
        if said.isdigit():
            return min(timedelta(seconds=int(said)), MAX_RETRY_AFTER)
        try:
            when = parsedate_to_datetime(said)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        wait = when - (now or datetime.now(UTC))
        return min(max(wait, timedelta(0)), MAX_RETRY_AFTER)


class HttpClient(Protocol):
    """The port: send a request, answer what came back — any status. Raise
    `EndpointUnavailable` only when nothing usable answered."""

    def send(self, request: HttpRequest) -> HttpAnswer: ...


class ClientAuthentication(ABC):
    """How a client proves who it is to an authorization server's endpoint — token,
    introspection, revocation, exchange, device authorization (RFC 6749 §2.3, RFC 7523).

    Context: one strategy reused by every grant, so `ClientSecretBasic`'s form-encoding of the
    id and secret before base64 — the bug most clients have — is written once.
    """

    client_id: str

    @abstractmethod
    def applied(
        self, fields: Sequence[tuple[str, str]], endpoint: str
    ) -> tuple[list[tuple[str, str]], dict[str, str]]:
        """The form fields and headers a request to `endpoint` carries once this client is
        authenticated in them."""
