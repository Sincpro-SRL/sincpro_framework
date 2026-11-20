"""`ScriptedHttp`: auth's `HttpClient` answering from a script — an identity provider, a token
endpoint or a policy engine in a test, with no network.

    http = ScriptedHttp({
        "https://idp/.well-known/openid-configuration": {"issuer": "https://idp", "jwks_uri": …},
        "POST https://idp/token": lambda request: ScriptedHttp.answer({"access_token": "t"}),
        "https://idp/certs": EndpointUnavailable("https://idp/certs", "could not be reached"),
    })
    JwtProvider.from_issuer("https://idp", audience="billing", http=http)
    http.count("https://idp/certs")   ->  how many times the keys were fetched

Context: a route is a URL — or `"METHOD URL"` for one method — answered by a dict (JSON, 200),
an `HttpAnswer`, a function of the request, or an exception to raise. A URL not scripted answers
404, as a server would. Every request is kept in `sent`, in order, thread-safe — a test counts
fetches under racing callers.
"""

import json
import threading
from collections.abc import Callable, Mapping
from typing import Any, Self
from urllib.parse import urlsplit, urlunsplit

from sincpro_framework.auth.domain.http import HttpAnswer, HttpRequest

type Scripted = HttpAnswer | Mapping[str, Any] | Callable[
    [HttpRequest], HttpAnswer
] | Exception


class ScriptedHttp:
    """An `HttpClient` answering what it was scripted to, and remembering what was sent."""

    def __init__(self, routes: Mapping[str, Scripted] | None = None) -> None:
        self.routes: dict[str, Scripted] = dict(routes or {})
        self.sent: list[HttpRequest] = []
        self._lock = threading.Lock()

    @staticmethod
    def answer(
        body: Any, status: int = 200, headers: Mapping[str, str] | None = None
    ) -> HttpAnswer:
        """A JSON answer — what most of an authorization server says."""
        return HttpAnswer(
            status=status,
            headers={"content-type": "application/json", **(headers or {})},
            body=json.dumps(body).encode(),
        )

    def route(self, target: str, scripted: Scripted) -> Self:
        """Script `target` — a URL, or `"METHOD URL"` — from now on."""
        with self._lock:
            self.routes[target] = scripted
        return self

    def _scripted(self, request: HttpRequest) -> Scripted | None:
        """The script for `request`: its method and URL, its URL, then both without the query."""
        parts = urlsplit(request.url)
        bare = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        for key in (
            f"{request.method} {request.url}",
            request.url,
            f"{request.method} {bare}",
            bare,
        ):
            if key in self.routes:
                return self.routes[key]
        return None

    def send(self, request: HttpRequest) -> HttpAnswer:
        """The scripted answer to `request` — 404 when nothing is scripted for it."""
        with self._lock:
            self.sent.append(request)
            scripted = self._scripted(request)
        if scripted is None:
            return HttpAnswer(status=404, body=b"not scripted")
        if isinstance(scripted, Exception):
            raise scripted
        if isinstance(scripted, HttpAnswer):
            return scripted
        if isinstance(scripted, Mapping):
            return self.answer(scripted)
        return scripted(request)

    def count(self, url: str, method: str | None = None) -> int:
        """How many requests went to `url` — any query — with `method` when named."""
        with self._lock:
            return sum(
                1
                for one in self.sent
                if one.target == HttpRequest(url=url).target
                and (method is None or one.method == method)
            )
