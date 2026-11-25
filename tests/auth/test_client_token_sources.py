"""The token sources at their edges: a 401 landing while a grant is refreshed, tokens that live
less than the skew, a device code that is slowed down or expires, exchanges that differ only in
what they ask, a subject token that expires first, tenants resolved at once or evicted mid-call,
an endpoint's wait forgotten once over, and a client that signs its assertion with its own key.
"""

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

import httpx
import pytest

from sincpro_framework.auth import CredentialsRejected, EndpointUnavailable, Identity
from sincpro_framework.auth.client import (
    ClientCredentials,
    ClientSecretBasic,
    DeviceAuthorization,
    DeviceAuthorizationPrompt,
    DpopProver,
    InMemoryOAuthGrants,
    OutboundCredentialsProvider,
    OutgoingRequest,
    PrivateKeyJwt,
    RefreshTokenSource,
    StaticToken,
    TenantTokenSources,
    TokenCache,
    TokenEndpoint,
    TokenExchange,
    TokenSource,
    TokenTypeIdentifier,
    call_with_credentials,
    httpx_auth,
)
from sincpro_framework.auth.common.adapters.jose_keys import PrivateKey
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.http import HttpRequest
from sincpro_framework.auth.common.infrastructure import jws
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.runtime.testing import FakeTokenEndpoint, ManualClock

API = OutgoingRequest(url="https://odoo.acme.bo/json/2/res.partner")
CLIENT = ClientSecretBasic("billing", "s3cret")
TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"


def consented(server: FakeTokenEndpoint):
    return TokenEndpoint(server.token_endpoint, CLIENT, server).request_token(
        [("grant_type", "password")]
    )


class InvalidatingDuringRefresh:
    """The token endpoint, with the API's 401 for the old access token landing while the
    refresh is on its way."""

    def __init__(self, server: FakeTokenEndpoint) -> None:
        self.server = server
        self.source: RefreshTokenSource | None = None
        self.invalidated: list[bool] = []

    def send(self, request: HttpRequest):
        if not self.invalidated and b"grant_type=refresh_token" in (request.body or b""):
            assert self.source is not None
            self.invalidated.append(
                self.source.invalidate(API, {"authorization": "Bearer at-1"})
            )
        return self.server.send(request)


def test_a_401_during_a_refresh_never_loses_the_rotated_refresh_token() -> None:
    clock = ManualClock(datetime.now(UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    http = InvalidatingDuringRefresh(server)
    grants = InMemoryOAuthGrants()
    odoo = RefreshTokenSource(
        "odoo",
        grants,
        CLIENT,
        token_endpoint=server.token_endpoint,
        http=http,
        store=InMemoryKeyValue(),
        now=clock.now,
        sleep=lambda _: None,
    )
    http.source = odoo
    odoo.store_grant(consented(server))
    clock.advance(seconds=290)
    assert odoo.headers_for(API) == {"authorization": "Bearer at-2"}
    assert http.invalidated == [True]
    stored = grants.get("odoo")
    assert stored is not None and stored.refresh_token == "rt-2"
    clock.advance(seconds=290)
    assert odoo.headers_for(API) == {"authorization": "Bearer at-3"}
    assert server.grants("refresh_token") == 2


def test_a_token_living_less_than_the_skew_is_used_for_half_its_life() -> None:
    clock = ManualClock(datetime.now(UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"}, expires_in=20)
    odoo = RefreshTokenSource(
        "odoo",
        InMemoryOAuthGrants(),
        CLIENT,
        token_endpoint=server.token_endpoint,
        http=server,
        store=InMemoryKeyValue(),
        now=clock.now,
    )
    odoo.store_grant(consented(server))
    assert [odoo.headers_for(API) for _ in range(3)] == [{"authorization": "Bearer at-1"}] * 3
    assert server.grants("refresh_token") == 0
    clock.advance(seconds=11)
    assert odoo.headers_for(API) == {"authorization": "Bearer at-2"}
    bank = ClientCredentials(
        CLIENT,
        token_endpoint=server.token_endpoint,
        http=server,
        cache=TokenCache(InMemoryKeyValue(now=clock.now), now=clock.now),
        now=clock.now,
    )
    first = bank.headers_for(API)
    assert [bank.headers_for(API) for _ in range(3)] == [first] * 3
    assert server.grants("client_credentials") == 1


def test_storing_a_grant_again_moves_its_version_on() -> None:
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    grants = InMemoryOAuthGrants()
    odoo = RefreshTokenSource("odoo", grants, CLIENT, token_endpoint=server.token_endpoint)
    odoo.store_grant(consented(server))
    odoo.store_grant(consented(server))
    stored = grants.get("odoo")
    assert stored is not None and stored.version == 1 and stored.refresh_token == "rt-2"


def device(server: FakeTokenEndpoint, clock: ManualClock, slept: list[float], **options):
    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock.advance(seconds=int(seconds))

    return DeviceAuthorization(
        options.pop("on_prompt", lambda prompt: None),
        client_id="sincpro-cli",
        issuer=server.issuer,
        http=server,
        sleep=sleep,
        now=clock.now,
        **options,
    )


def test_a_device_slows_down_when_told_and_gives_up_when_its_code_expires() -> None:
    server = FakeTokenEndpoint(clients={"sincpro-cli": ""})
    clock = ManualClock(datetime.now(UTC))
    slept: list[float] = []
    server.fail_next(400, "slow_down")
    with pytest.raises(exceptions.GrantRejected, match="expired_token"):
        device(server, clock, slept).authorize()
    assert slept[0] == 6 and set(slept) == {6}
    assert sum(slept) >= 600 and len(slept) == 100


def test_a_device_code_the_server_says_expired_is_authorized_again() -> None:
    server = FakeTokenEndpoint(clients={"sincpro-cli": ""})
    clock = ManualClock(datetime.now(UTC))
    server.fail_next(400, "expired_token")
    with pytest.raises(exceptions.GrantRejected, match="expired_token"):
        device(server, clock, []).authorize()
    server.fail_next(400, "access_denied")
    with pytest.raises(exceptions.Unauthenticated, match="refused"):
        device(server, clock, []).authorize()


def test_a_device_authorization_without_a_user_code_is_this_services_to_fix() -> None:
    server = FakeTokenEndpoint(clients={"sincpro-cli": ""})
    server.route(
        f"POST {server.device_endpoint}",
        {"device_code": "dc-1", "verification_uri": f"{server.issuer}/activate"},
    )
    shown: list[DeviceAuthorizationPrompt] = []
    with pytest.raises(CredentialsRejected, match="malformed_answer"):
        device(server, ManualClock(datetime.now(UTC)), [], on_prompt=shown.append).authorize()
    assert shown == []


def exchange(server, cache, **options) -> TokenExchange:
    return TokenExchange(
        "orders-api",
        CLIENT,
        token_endpoint=server.token_endpoint,
        http=server,
        cache=cache,
        subject=options.pop("subject", lambda request: ("ana-token", "access")),
        **options,
    )


def test_exchanges_differing_in_token_type_or_actor_never_share_a_token() -> None:
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    cache = TokenCache(InMemoryKeyValue())
    plain = exchange(server, cache)
    as_jwt = exchange(server, cache, requested_token_type=TokenTypeIdentifier.JWT)
    with_actor = exchange(server, cache, actor=StaticToken("svc-token"))
    seen = {
        one.headers_for(API)["authorization"] for one in (plain, as_jwt, with_actor, plain)
    }
    assert seen == {"Bearer at-1", "Bearer at-2", "Bearer at-3"}
    assert server.grants(TOKEN_EXCHANGE) == 3


def test_an_exchanged_token_is_never_cached_past_the_subject_token() -> None:
    clock = ManualClock(datetime(2026, 11, 20, tzinfo=UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"}, expires_in=300)
    expiring = int((clock.now() + timedelta(seconds=60)).timestamp())
    subject = jws.encode({"alg": "none"}, {"sub": "ana", "exp": expiring}, lambda _: b"")
    orders = exchange(
        server,
        TokenCache(InMemoryKeyValue(now=clock.now), now=clock.now),
        subject=lambda request: (subject, TokenTypeIdentifier.ACCESS_TOKEN),
        now=clock.now,
    )
    assert orders.headers_for(API) == {"authorization": "Bearer at-1"}
    clock.advance(seconds=29)
    assert orders.headers_for(API) == {"authorization": "Bearer at-1"}
    clock.advance(seconds=2)
    assert orders.headers_for(API) == {"authorization": "Bearer at-2"}


def test_a_subject_token_refused_as_an_invalid_request_is_the_callers_to_fix() -> None:
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    refusal = {"error": "invalid_request", "error_description": "subject_token expired"}
    server.route(f"POST {server.token_endpoint}", server.answer(refusal, status=400))
    with pytest.raises(exceptions.Unauthenticated, match="cannot be exchanged"):
        exchange(server, TokenCache(InMemoryKeyValue())).headers_for(API)
    malformed = {"error": "invalid_request", "error_description": "audience is missing"}
    server.route(f"POST {server.token_endpoint}", server.answer(malformed, status=400))
    with pytest.raises(CredentialsRejected) as refused:
        exchange(server, TokenCache(InMemoryKeyValue())).headers_for(API)
    assert not isinstance(refused.value, exceptions.Unauthenticated)


def test_an_exchange_proves_its_dpop_key_to_the_token_endpoint() -> None:
    server = FakeTokenEndpoint(clients={"billing": "s3cret"}, dpop_nonce="n-1")
    orders = exchange(server, TokenCache(InMemoryKeyValue()), dpop=DpopProver())
    assert orders.headers_for(API)["authorization"].startswith("Bearer at-")
    assert jws.parse(server.sent[-1].headers["dpop"]).claims["nonce"] == "n-1"


def test_an_expires_in_sent_as_text_is_read() -> None:
    clock = ManualClock(datetime(2026, 11, 20, tzinfo=UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    server.route(
        f"POST {server.token_endpoint}",
        {"access_token": "at-x", "token_type": "Bearer", "expires_in": "3599"},
    )
    issued = TokenEndpoint(
        server.token_endpoint, CLIENT, server, now=clock.now
    ).request_token([("grant_type", "client_credentials")])
    assert issued.expires_at - issued.received_at == timedelta(seconds=3599)


def test_tenants_resolved_at_once_share_one_source() -> None:
    barrier = threading.Barrier(8)

    def resolve(tenant: str) -> TokenSource:
        time.sleep(0.02)
        return StaticToken(f"{tenant}-token")

    sources = TenantTokenSources(resolve)
    found: list[TokenSource] = []

    def call() -> None:
        barrier.wait()
        found.append(sources.source_for_tenant("bo"))

    threads = [threading.Thread(target=call) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({id(one) for one in found}) == 1


def test_a_tenant_evicted_mid_call_still_drops_the_token_its_api_refused() -> None:
    clock = ManualClock(datetime(2026, 11, 20, tzinfo=UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    store = InMemoryKeyValue(now=clock.now)
    sources = TenantTokenSources(
        lambda tenant: ClientCredentials(
            CLIENT,
            token_endpoint=server.token_endpoint,
            scope=[tenant],
            http=server,
            cache=TokenCache(store, now=clock.now),
            now=clock.now,
        ),
        max_tenants=1,
    )
    api = server.resource()
    for_bo = API.model_copy(update={"tenant": "bo"})
    assert call_with_credentials(sources, for_bo, api).status == 200
    server.revoked.add("at-1")
    clock.advance(seconds=30)

    def evicting(headers):
        sources.headers_for(API.model_copy(update={"tenant": "pe"}))
        return api(headers)

    answered = call_with_credentials(sources, for_bo, evicting)
    assert answered.status == 200 and answered.json_object()["token"] == "at-3"


def test_an_endpoints_wait_is_forgotten_once_it_is_over() -> None:
    clock = ManualClock(datetime(2026, 11, 20, tzinfo=UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    cache = TokenCache(InMemoryKeyValue(now=clock.now), now=clock.now)
    for tenant in ("a", "b"):
        source = ClientCredentials(
            CLIENT,
            token_endpoint=server.token_endpoint,
            scope=[tenant],
            http=server,
            cache=cache,
            now=clock.now,
        )
        server.fail_next(503, retry_after=30)
        with pytest.raises(EndpointUnavailable):
            source.headers_for(API)
        clock.advance(seconds=31)
    assert len(cache._backoffs) == 1
    source.headers_for(API)
    assert cache._backoffs == {}


def test_private_key_jwt_authenticates_this_service_to_a_token_endpoint() -> None:
    server = FakeTokenEndpoint(clients={"billing": "unused"})
    key = PrivateKey.generate("ES256", key_id="k1")
    bank = ClientCredentials(
        client=PrivateKeyJwt("billing", key),
        token_endpoint=server.token_endpoint,
        http=server,
        cache=TokenCache(InMemoryKeyValue()),
    )
    assert bank.headers_for(API) == {"authorization": "Bearer at-1"}
    form = dict(parse_qsl((server.sent[-1].body or b"").decode()))
    assertion = jws.parse(form["client_assertion"])
    assert assertion.header["kid"] == "k1" and "authorization" not in server.sent[-1].headers
    assert (assertion.claims["iss"], assertion.claims["aud"]) == (
        "billing",
        server.token_endpoint,
    )
    assert key.public().verify(assertion.signing_input, assertion.signature)


def test_the_outbound_provider_presents_nothing_for_a_call_with_no_audience() -> None:
    asked: list[OutgoingRequest] = []

    class Recording(StaticToken):
        def headers_for(self, request: OutgoingRequest) -> dict[str, str]:
            asked.append(request)
            return super().headers_for(request)

    provider = OutboundCredentialsProvider({"orders": Recording("t-1"), "": Recording("t-0")})
    ana = Identity.user("user:ana", tenant="bo")
    assert provider.outbound_for(ana) is None
    assert provider.outbound_for(ana, "billing") is None
    assert asked == []
    issued = provider.outbound_for(ana, "orders")
    assert issued is not None and issued.headers == {"authorization": "Bearer t-1"}
    assert asked[0].tenant == "bo"


class Unchanging(TokenSource):
    """Answers that asking again may help — and then the same headers."""

    def headers_for(self, request: OutgoingRequest) -> dict[str, str]:
        return {"authorization": "Bearer t-1"}

    def invalidate(self, request, presented, answer=None) -> bool:
        return True


def test_httpx_never_sends_the_same_refused_headers_again() -> None:
    sent: list[str] = []

    def api(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers["authorization"])
        return httpx.Response(401)

    with httpx.Client(
        transport=httpx.MockTransport(api), auth=httpx_auth(Unchanging())
    ) as client:
        assert client.get("https://api.bank.bo/x").status_code == 401

    async def run() -> int:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(api), auth=httpx_auth(Unchanging())
        ) as client:
            return (await client.get("https://api.bank.bo/x")).status_code

    assert asyncio.run(run()) == 401
    assert sent == ["Bearer t-1", "Bearer t-1"]
