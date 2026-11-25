"""Acting for someone, and holding consent: a token exchanged for the caller's own (RFC 8693),
carried to the next service with no change to its transports; a refresh token rotated by one
replica at a time, never reused; consent obtained by device code (RFC 8628)."""

import threading
from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    Credentials,
    GrantRejected,
    Identity,
    security,
)
from sincpro_framework.auth.client import (
    ClientSecretBasic,
    DeviceAuthorization,
    DeviceAuthorizationPrompt,
    InMemoryOAuthGrants,
    KeyValueOAuthGrants,
    OAuthGrant,
    OutboundCredentialsProvider,
    OutgoingRequest,
    PublicClient,
    RefreshTokenSource,
    TokenCache,
    TokenEndpoint,
    TokenExchange,
)
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.infrastructure.security_context import as_presented_token
from sincpro_framework.auth.server import StaticProvider
from sincpro_framework.data_layer.store import InMemoryKeyValue, Leases
from sincpro_framework.runtime.testing import FakeTokenEndpoint, ManualClock

API = OutgoingRequest(url="https://odoo.acme.bo/json/2/res.partner")
CLIENT = ClientSecretBasic("billing", "s3cret")


def consented(server: FakeTokenEndpoint):
    return TokenEndpoint(server.token_endpoint, CLIENT, server).request_token(
        [("grant_type", "password")]
    )


def refreshing(server, grants, clock, store, **options) -> RefreshTokenSource:
    return RefreshTokenSource(
        "odoo:tenant-bo",
        grants,
        CLIENT,
        token_endpoint=server.token_endpoint,
        http=server,
        store=store,
        now=clock.now,
        sleep=lambda _: None,
        **options,
    )


def test_a_held_grant_is_used_until_due_then_refreshed_and_rotated() -> None:
    clock = ManualClock(datetime.now(UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    grants = InMemoryOAuthGrants()
    odoo = refreshing(server, grants, clock, InMemoryKeyValue())
    odoo.store_grant(consented(server))
    assert odoo.headers_for(API) == {"authorization": "Bearer at-1"}
    clock.advance(seconds=290)
    assert odoo.headers_for(API) == {"authorization": "Bearer at-2"}
    held = grants.get("odoo:tenant-bo")
    assert held is not None and held.refresh_token == "rt-2" and held.version == 1


def test_replicas_racing_to_refresh_refresh_once_and_never_reuse() -> None:
    clock = ManualClock(datetime.now(UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"}, delay=0.1)
    grants, leases = InMemoryOAuthGrants(), InMemoryKeyValue()
    replicas = [refreshing(server, grants, clock, leases) for _ in range(8)]
    replicas[0].store_grant(consented(server))
    clock.advance(seconds=290)
    barrier = threading.Barrier(len(replicas))
    seen: list[str] = []

    def call(replica: RefreshTokenSource) -> None:
        barrier.wait()
        seen.append(replica.headers_for(API)["authorization"])

    threads = [threading.Thread(target=call, args=(one,)) for one in replicas]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert server.grants("refresh_token") == 1
    assert set(seen) == {"Bearer at-2"}


def test_a_grant_the_server_no_longer_honours_is_dropped() -> None:
    clock = ManualClock(datetime.now(UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    grants = InMemoryOAuthGrants()
    odoo = refreshing(server, grants, clock, InMemoryKeyValue())
    odoo.store_grant(consented(server))
    server.refresh.clear()
    clock.advance(seconds=290)
    with pytest.raises(GrantRejected):
        odoo.headers_for(API)
    assert grants.get("odoo:tenant-bo") is None
    with pytest.raises(exceptions.ProgrammingError, match="store_grant first"):
        odoo.headers_for(API)


def test_an_access_token_refused_by_the_api_is_refreshed_on_the_next_call() -> None:
    clock = ManualClock(datetime.now(UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    odoo = refreshing(server, InMemoryOAuthGrants(), clock, InMemoryKeyValue())
    odoo.store_grant(consented(server))
    assert odoo.invalidate(API, {"authorization": "Bearer at-1"})
    assert odoo.headers_for(API) == {"authorization": "Bearer at-2"}


def test_grants_on_a_store_are_written_by_compare_and_set_on_their_version() -> None:
    grants = KeyValueOAuthGrants(InMemoryKeyValue())
    first = OAuthGrant(refresh_token="rt-1", version=0)
    assert grants.compare_and_set("k", None, first)
    assert not grants.compare_and_set("k", None, first)
    second = first.model_copy(update={"refresh_token": "rt-2", "version": 1})
    assert grants.compare_and_set("k", 0, second) and not grants.compare_and_set(
        "k", 0, second
    )
    assert grants.get("k") == second
    assert grants.compare_and_set("k", 1, None) and grants.get("k") is None
    assert "rt-2" not in repr(second)


def exchange(server: FakeTokenEndpoint) -> TokenExchange:
    return TokenExchange(
        "orders-api",
        CLIENT,
        token_endpoint=server.token_endpoint,
        http=server,
        cache=TokenCache(InMemoryKeyValue()),
    )


class CommandBill(DataTransferObject):
    total: int = 1


class Billed(DataTransferObject):
    carried: dict[str, str]


def test_a_call_carries_a_token_exchanged_for_its_caller_to_the_next_service() -> None:
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    caller = Identity.user("user:ana", tenant="bo")
    auth = AccessControl(
        providers=[
            StaticProvider({"ana-token": caller}),
            OutboundCredentialsProvider({"orders": exchange(server)}),
        ]
    )
    billing = UseFramework("exchange-billing", log_after_execution=False)

    @billing.feature(CommandBill)
    @auth.authenticated
    class Bill(Feature):
        def execute(self, dto: CommandBill) -> Billed:
            return Billed(carried=security.identity_headers("orders"))

    auth.on(billing)
    credentials = Credentials(transport="http", headers={"authorization": "Bearer ana-token"})
    with security.authenticated_as(billing, credentials):
        first = billing(CommandBill(), Billed)
        again = billing(CommandBill(), Billed)
    assert first is not None and again is not None
    assert first.carried == again.carried == {"authorization": "Bearer at-1"}
    assert server.grants("urn:ietf:params:oauth:grant-type:token-exchange") == 1
    exchanged = dict(p.split("=") for p in (server.sent[-1].body or b"").decode().split("&"))
    assert exchanged["subject_token"] == "ana-token" and exchanged["audience"] == "orders-api"


def test_with_no_caller_there_is_nothing_to_exchange() -> None:
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    with pytest.raises(exceptions.ProgrammingError, match="none was presented"):
        exchange(server).headers_for(API)


def test_a_callers_token_the_server_will_not_exchange_is_the_callers_to_fix() -> None:
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    server.revoked.add("stolen")
    with security.as_identity(Identity.user("user:x")):
        with as_presented_token("stolen"), pytest.raises(
            exceptions.Unauthenticated, match="cannot be exchanged"
        ):
            exchange(server).headers_for(API)


def test_a_device_is_authorized_by_a_code_shown_to_a_person() -> None:
    server = FakeTokenEndpoint(clients={"sincpro-cli": ""})
    shown: list[DeviceAuthorizationPrompt] = []
    polls = {"count": 0}

    def sleep(_: float) -> None:
        polls["count"] += 1
        if polls["count"] == 2:
            server.approve(shown[0].user_code)

    issued = DeviceAuthorization(
        shown.append,
        client_id="sincpro-cli",
        issuer=server.issuer,
        http=server,
        sleep=sleep,
    ).authorize()
    assert shown[0].verification_uri.endswith("/activate")
    assert issued.access_token.get_secret_value().startswith("at-") and issued.refresh_token
    clock = ManualClock(datetime.now(UTC))
    cli = RefreshTokenSource(
        "cli",
        InMemoryOAuthGrants(),
        PublicClient("sincpro-cli"),
        token_endpoint=server.token_endpoint,
        http=server,
        store=InMemoryKeyValue(),
        now=clock.now,
    )
    cli.store_grant(issued)
    assert (
        cli.headers_for(API)["authorization"]
        == f"Bearer {issued.access_token.get_secret_value()}"
    )


def test_an_app_behind_the_middleware_can_exchange_the_token_it_was_called_with() -> None:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from sincpro_framework.auth import IdentityMiddleware

    async def presented(request: Request) -> PlainTextResponse:
        return PlainTextResponse(security.presented or "")

    app = Starlette(routes=[Route("/presented", presented)])
    app.add_middleware(
        IdentityMiddleware,
        access=AccessControl(
            providers=[StaticProvider({"t-ana": Identity.user("user:ana")})]
        ),
    )
    answered = TestClient(app).get("/presented", headers={"authorization": "Bearer t-ana"})
    assert answered.text == "t-ana"


def test_a_replica_never_refreshes_while_another_holds_the_lease() -> None:
    clock = ManualClock(datetime.now(UTC))
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    store = InMemoryKeyValue()
    odoo = refreshing(
        server, InMemoryOAuthGrants(), clock, store, lease_timeout=timedelta(milliseconds=20)
    )
    odoo.store_grant(consented(server))
    clock.advance(seconds=290)
    held = Leases(store, prefix="sincpro:auth:refreshing").take(
        "odoo:tenant-bo", ttl=timedelta(minutes=1)
    )
    assert held is not None  # another replica, refreshing slowly
    with pytest.raises(exceptions.EndpointUnavailable, match="still running"):
        odoo.headers_for(API)
    assert server.grants("refresh_token") == 0
