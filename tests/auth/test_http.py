"""The standard library's HTTP client auth speaks through: any status comes back as an answer, a
redirect is never followed, plain http only reaches the loopback, and a server that does not
answer — or answers too much — is `EndpointUnavailable`, never a refusal of the caller."""

import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sincpro_framework.auth import EndpointUnavailable, HttpAnswer, HttpRequest, UrllibHttp
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.http import redact_url

hits: list[str] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return None

    def _answer(
        self, status: int, body: bytes, headers: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        hits.append(self.path)
        if self.path == "/ok":
            self._answer(200, b'{"a": 1}', {"Content-Type": "application/json"})
        elif self.path == "/refused":
            self._answer(400, b'{"error": "invalid_client"}')
        elif self.path == "/down":
            self._answer(503, b"down", {"Retry-After": "30"})
        elif self.path == "/moved":
            self._answer(302, b"", {"Location": "/secret-elsewhere"})
        elif self.path == "/big":
            self._answer(200, b"x" * 2048)
        elif self.path == "/slow":
            threading.Event().wait(1)
            self._answer(200, b"late")
        else:
            self._answer(404, b"")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        received = self.rfile.read(length)
        self._answer(
            200,
            json.dumps(
                {"got": received.decode(), "type": self.headers["Content-Type"]}
            ).encode(),
        )


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_any_status_comes_back_as_an_answer(server: str) -> None:
    http = UrllibHttp()
    assert http.send(HttpRequest(url=f"{server}/ok")).json_object() == {"a": 1}
    refused = http.send(HttpRequest(url=f"{server}/refused"))
    assert (refused.status, refused.json_object()) == (400, {"error": "invalid_client"})
    down = http.send(HttpRequest(url=f"{server}/down"))
    assert (down.status, down.retry_after()) == (503, timedelta(seconds=30))


def test_a_redirect_is_answered_never_followed(server: str) -> None:
    hits.clear()
    answer = UrllibHttp().send(HttpRequest(url=f"{server}/moved"))
    assert answer.status == 302 and "/secret-elsewhere" not in hits


def test_a_form_is_posted_url_encoded(server: str) -> None:
    answer = UrllibHttp().send(
        HttpRequest.form(
            f"{server}/token", [("grant_type", "client_credentials"), ("a", "b c")]
        )
    )
    assert answer.json_object() == {
        "got": "grant_type=client_credentials&a=b+c",
        "type": "application/x-www-form-urlencoded",
    }


def test_too_much_too_late_or_nobody_there_is_unavailable(server: str) -> None:
    with pytest.raises(EndpointUnavailable, match="more than 1024 bytes"):
        UrllibHttp(max_bytes=1024).send(HttpRequest(url=f"{server}/big"))
    with pytest.raises(EndpointUnavailable):
        UrllibHttp().send(HttpRequest(url=f"{server}/slow", timeout=timedelta(seconds=0.2)))
    with pytest.raises(EndpointUnavailable):
        UrllibHttp().send(HttpRequest(url="http://127.0.0.1:9/nobody"))


def test_plain_http_reaches_only_the_loopback() -> None:
    with pytest.raises(exceptions.ProgrammingError, match="not https"):
        UrllibHttp().send(HttpRequest(url="http://auth.acme.bo/token"))
    with pytest.raises(exceptions.ProgrammingError):
        UrllibHttp().send(HttpRequest(url="ftp://auth.acme.bo/token"))


def test_a_request_never_says_its_secrets() -> None:
    request = HttpRequest.form(
        "https://client:pw@auth.acme.bo/token?code=abc#x",
        [("client_secret", "s3cret")],
        headers={"authorization": "Basic czNjcmV0"},
    )
    assert "s3cret" not in repr(request) and "czNjcmV0" not in repr(request)
    assert "pw" not in repr(request) and "abc" not in repr(request)
    assert request.target == "https://auth.acme.bo/token"
    assert redact_url("https://[::1]:8443/p?q") == "https://[::1]:8443/p"


def test_retry_after_is_read_as_seconds_or_a_date_and_capped() -> None:
    now = datetime(2026, 11, 20, 12, tzinfo=UTC)
    assert HttpAnswer(
        status=503, headers={"Retry-After": "86400"}
    ).retry_after() == timedelta(minutes=5)
    dated = HttpAnswer(status=503, headers={"retry-after": "Fri, 20 Nov 2026 12:01:00 GMT"})
    assert dated.retry_after(now) == timedelta(minutes=1)
    assert HttpAnswer(status=503, headers={"retry-after": "soon"}).retry_after() is None
    assert HttpAnswer(status=503).retry_after() is None
