"""What this service presents when it calls another: a token fetched once for every thread and
replica, served until shortly before it expires, fetched again once when the API refuses it — never
in a loop, never across tenants — and the token endpoint's refusals said as this service's own.
"""

import asyncio
import threading
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import requests

from sincpro_framework.auth import (
    ApiKeyHeader,
    BasicCredentials,
    ClientCredentials,
    ClientSecretPost,
    CredentialsRejected,
    EndpointUnavailable,
    Identity,
    NoCredentialsFor,
    OutgoingRequest,
    StaticToken,
    TokenCache,
    TokenSource,
    TokenSources,
    call_with_credentials,
    httpx_auth,
    requests_auth,
    security,
)
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.runtime.testing import (
    FakeTokenEndpoint,
    ManualClock,
    TokenSourceContract,
)

API = OutgoingRequest(url="https://api.bank.bo/qr", method="POST")


def bank(
    server: FakeTokenEndpoint, cache: TokenCache | None = None, **options
) -> ClientCredentials:
    return ClientCredentials(
        client_id="billing",
        client_secret="s3cret",
        issuer=server.issuer,
        scope=["payments"],
        http=server,
        cache=cache or TokenCache(InMemoryKeyValue()),
        **options,
    )


def test_static_credentials_are_sent_as_given() -> None:
    assert StaticToken("t-1", header="apikey", scheme="TokenApi").headers_for(API) == {
        "apikey": "TokenApi t-1"
    }
    assert ApiKeyHeader("k-1", header="odoo-api-key").headers_for(API) == {
        "odoo-api-key": "k-1"
    }
    assert BasicCredentials("billing", "pw").headers_for(API) == {
        "authorization": "Basic YmlsbGluZzpwdw=="
    }
    with pytest.raises(exceptions.ProgrammingError):
        BasicCredentials("a:b", "pw")
    assert not StaticToken("t-1").invalidate(API, {"authorization": "Bearer t-1"})


def test_a_token_is_fetched_once_and_shared() -> None:
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    source = bank(server)
    first = source.headers_for(API)
    assert first == {"authorization": "Bearer at-1"}
    assert [source.headers_for(API) for _ in range(5)] == [first] * 5
    assert server.grants("client_credentials") == 1
    sent = server.sent[-1]
    assert b"scope=payments" in (sent.body or b"") and sent.headers[
        "authorization"
    ].startswith("Basic ")


def test_sixteen_callers_at_once_cause_one_fetch() -> None:
    server = FakeTokenEndpoint(clients={"billing": "s3cret"}, delay=0.2)
    source = bank(server)
    barrier = threading.Barrier(16)
    seen: list[dict[str, str]] = []

    def call() -> None:
        barrier.wait()
        seen.append(source.headers_for(API))

    threads = [threading.Thread(target=call) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert server.grants() == 1 and len({one["authorization"] for one in seen}) == 1


def test_two_replicas_on_one_store_share_one_token() -> None:
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    shared = InMemoryKeyValue()
    one, other = bank(server, TokenCache(shared)), bank(server, TokenCache(shared))
    assert one.headers_for(API) == other.headers_for(API)
    assert server.grants() == 1


def test_a_token_is_fetched_again_shortly_before_it_expires() -> None:
    clock = ManualClock(datetime(2026, 11, 20, tzinfo=UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"}, expires_in=300)
    source = bank(
        server, TokenCache(InMemoryKeyValue(now=clock.now), now=clock.now), now=clock.now
    )
    assert source.headers_for(API)["authorization"] == "Bearer at-1"
    clock.advance(seconds=269)
    assert source.headers_for(API)["authorization"] == "Bearer at-1"
    clock.advance(seconds=2)
    assert source.headers_for(API)["authorization"] == "Bearer at-2"


def test_a_refused_token_is_fetched_again_once_and_the_call_sent_again() -> None:
    clock = ManualClock(datetime(2026, 11, 20, tzinfo=UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    source = bank(
        server, TokenCache(InMemoryKeyValue(now=clock.now), now=clock.now), now=clock.now
    )
    api = server.resource()
    assert call_with_credentials(source, API, api).status == 200
    server.revoked.add("at-1")
    clock.advance(seconds=30)
    answered = call_with_credentials(source, API, api)
    assert answered.status == 200 and answered.json_object()["token"] == "at-2"
    assert [one["authorization"] for one in api.calls] == [
        "Bearer at-1",
        "Bearer at-1",
        "Bearer at-2",
    ]


def test_an_api_refusing_every_token_costs_one_retry_never_a_fetch_per_request() -> None:
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    source = bank(server)

    def refusing(headers: Mapping[str, str]):
        return server.answer({"error": "wrong audience"}, status=401)

    for _ in range(5):
        assert call_with_credentials(source, API, refusing).status == 401
    assert server.grants() == 1


def test_a_client_the_server_refuses_is_this_services_fault_never_the_callers() -> None:
    server = FakeTokenEndpoint(clients={"billing": "another secret"})
    with pytest.raises(CredentialsRejected, match="invalid_client") as refused:
        bank(server).headers_for(API)
    assert not isinstance(refused.value, exceptions.Unauthenticated)
    assert "s3cret" not in str(refused.value)


def test_a_server_asking_to_wait_is_not_asked_before_then() -> None:
    clock = ManualClock(datetime(2026, 11, 20, tzinfo=UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    source = bank(
        server, TokenCache(InMemoryKeyValue(now=clock.now), now=clock.now), now=clock.now
    )
    server.fail_next(503, retry_after=30)
    with pytest.raises(EndpointUnavailable) as refused:
        source.headers_for(API)
    for _ in range(2):
        with pytest.raises(EndpointUnavailable):
            source.headers_for(API)
    assert refused.value.retry_after == timedelta(seconds=30)
    assert server.grants() == 0 and server.count(server.token_endpoint) == 1
    clock.advance(seconds=31)
    assert source.headers_for(API)["authorization"] == "Bearer at-1"


def test_credentials_per_tenant_are_never_another_tenants() -> None:
    sources = TokenSources({"bo": StaticToken("bo-token"), "pe": StaticToken("pe-token")})
    with security.as_identity(Identity.user("user:ana", tenant="pe")):
        answer = call_with_credentials(sources, API, lambda headers: headers)
    assert answer == {"authorization": "Bearer pe-token"}
    with pytest.raises(NoCredentialsFor):
        sources.headers_for(API.model_copy(update={"tenant": "cl"}))
    with pytest.raises(exceptions.ProgrammingError, match="names no tenant"):
        sources.headers_for(API)
    resolved = TokenSources(
        lambda tenant: StaticToken(f"{tenant}-t") if tenant != "x" else None, max_tenants=2
    )
    for tenant in ("a", "b", "c"):
        resolved.headers_for(API.model_copy(update={"tenant": tenant}))
    assert resolved.describe()["tenants"] == 2


class Rotating(TokenSource):
    """t-1 until refused, then t-2."""

    def __init__(self) -> None:
        self.current = "t-1"

    def headers_for(self, request: OutgoingRequest) -> dict[str, str]:
        return {"authorization": f"Bearer {self.current}"}

    def invalidate(self, request, presented, answer=None) -> bool:
        self.current = "t-2"
        return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return None

    def do_GET(self) -> None:
        status = 200 if self.headers.get("authorization") == "Bearer t-2" else 401
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture(scope="module")
def server_url() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_requests_sends_once_more_with_new_credentials(server_url: str) -> None:
    answered = requests.get(f"{server_url}/x", auth=requests_auth(Rotating()), timeout=5)
    assert answered.status_code == 200 and len(answered.history) == 1


def test_httpx_sends_once_more_with_new_credentials_sync_and_async() -> None:
    def api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200 if request.headers["authorization"] == "Bearer t-2" else 401
        )

    with httpx.Client(
        transport=httpx.MockTransport(api), auth=httpx_auth(Rotating())
    ) as client:
        assert client.get("https://api.bank.bo/x").status_code == 200

    async def run() -> int:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(api), auth=httpx_auth(Rotating())
        ) as client:
            return (await client.get("https://api.bank.bo/x")).status_code

    assert asyncio.run(run()) == 200


class TestClientCredentialsContract(TokenSourceContract):
    def make_source(self) -> TokenSource:
        return bank(FakeTokenEndpoint(clients={"billing": "s3cret"}))

    def secret_values(self) -> list[str]:
        return ["s3cret"]

    def make_source_whose_endpoint_is_down(self) -> TokenSource | None:
        server = FakeTokenEndpoint(clients={"billing": "s3cret"})
        server.route(
            f"POST {server.token_endpoint}",
            EndpointUnavailable(server.token_endpoint, "down"),
        )
        return bank(server)


class TestPostedClientContract(TokenSourceContract):
    def make_source(self) -> TokenSource:
        server = FakeTokenEndpoint(clients={"billing": "s3cret"})
        return ClientCredentials(
            client=ClientSecretPost("billing", "s3cret"),
            token_endpoint=server.token_endpoint,
            http=server,
            cache=TokenCache(InMemoryKeyValue()),
        )

    def secret_values(self) -> list[str]:
        return ["s3cret"]
