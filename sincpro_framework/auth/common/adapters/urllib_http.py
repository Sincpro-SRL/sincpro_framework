"""`UrllibHttp`: auth's `HttpClient` on the standard library — https only, no redirect ever
followed, the body counted against a size.

    UrllibHttp().send(HttpRequest(url="https://auth.acme.bo/realms/a/.well-known/openid-configuration"))
        ->  HttpAnswer(status=200, ...)

Context: what these requests carry is a client's secret, a token being introspected, an
assertion — so a redirect is answered as the 3xx it is, never followed (`urllib` would resend
the headers to wherever it points), and plain `http` is refused except on the loopback, where a
local identity provider lives during development. A status of any kind comes back as an
`HttpAnswer`; a host not reached, a time run out, a connection lost or a body past `max_bytes`
is `EndpointUnavailable` — the pattern of `object_storage`'s `HttpSource`.
"""

import ipaddress
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.http import HttpAnswer, HttpRequest

LOOPBACK_NAMES = frozenset({"localhost"})


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Every redirect answered as itself — `urllib` then raises it as an `HTTPError`."""

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ):
        """Never followed: the 3xx is answered as it came — a redirect would resend a client's
        credentials to another host."""
        return None


def is_loopback(host: str) -> bool:
    """Whether `host` names this machine — `localhost`, `127.0.0.0/8`, `::1`."""
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class UrllibHttp:
    """Requests sent with `urllib`, within their timeout, their answer capped at `max_bytes`."""

    def __init__(self, max_bytes: int = 1_048_576, allow_insecure: bool = False) -> None:
        """`allow_insecure` lets plain `http` reach any host — a test network, never production."""
        self.max_bytes = max_bytes
        self.allow_insecure = allow_insecure
        self._opener = urllib.request.build_opener(_NoRedirects())

    def _validate_url(self, request: HttpRequest) -> None:
        """A URL that is not https, nor http to the loopback, is a `ProgrammingError` — a
        configuration to fix, never a request to try."""
        parts = urlsplit(request.url)
        if parts.scheme == "https":
            return
        if parts.scheme == "http" and (
            self.allow_insecure or is_loopback(parts.hostname or "")
        ):
            return
        raise exceptions.ProgrammingError(
            f"{request.target} is not https: auth sends credentials only over TLS"
        )

    def _read(self, response: Any, request: HttpRequest) -> bytes:
        """The body, refused past `max_bytes` — read one byte further to know."""
        body = response.read(self.max_bytes + 1)
        if len(body) > self.max_bytes:
            raise exceptions.EndpointUnavailable(
                request.target, f"answered more than {self.max_bytes} bytes"
            )
        return body

    def send(self, request: HttpRequest) -> HttpAnswer:
        """The answer to `request`, whatever its status.

        1. A URL that is not https (nor http to the loopback): `ProgrammingError`.
        2. Sent within `request.timeout`; a status the server answered — a redirect, a 4xx, a
           5xx included — comes back as an `HttpAnswer` with its body.
        3. A host not reached, a time run out, a connection lost: `EndpointUnavailable`.
        Final: the answer, its body capped at `max_bytes`.
        """
        self._validate_url(request)
        prepared = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with self._opener.open(
                prepared, timeout=request.timeout.total_seconds()
            ) as response:
                return HttpAnswer(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=self._read(response, request),
                )
        except urllib.error.HTTPError as answered:
            try:
                return HttpAnswer(
                    status=answered.code,
                    headers=dict(answered.headers.items()) if answered.headers else {},
                    body=self._read(answered, request),
                )
            finally:
                answered.close()
        except (urllib.error.URLError, OSError) as error:
            raise exceptions.EndpointUnavailable(
                request.target, f"could not be reached: {error}"
            ) from error
