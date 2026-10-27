"""A test checks production's rules against providers of its own: `auth.using(providers=...)`
on a `fresh()` bus, `replaces=auth` — and a provider whose backend does not answer is a 503 to
retry, never a 401 nor a 500."""

import asyncio
import json
from typing import Any

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    AuthProvider,
    Credentials,
    Identity,
    IdentityMiddleware,
    Permission,
    StaticProvider,
    security,
)
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.exceptions import ProgrammingError, ServiceUnavailableError
from sincpro_framework.runtime.testing import AuthProviderContract


class LedgerPermission(Permission):
    POST = "ledger.entry.post"


class CommandPostEntry(DataTransferObject):
    amount: int


class ResponsePosted(DataTransferObject):
    by: str


class RegistryProvider(AuthProvider):
    """A provider that asks a registry who a token is — the registry production wires."""

    name = "registry"

    def __init__(self, registry: dict[str, Identity]) -> None:
        self.registry = registry

    def authenticate(self, credentials: Credentials) -> Identity | None:
        if credentials.bearer is None:
            return None
        found = self.registry.get(credentials.bearer)
        if found is None:
            raise exceptions.Unauthenticated("unknown token")
        return found.model_copy(update={"provider": self.name})


def bearer(token: str) -> Credentials:
    return Credentials(transport="http", headers={"authorization": f"Bearer {token}"})


def guarded() -> tuple[UseFramework, AccessControl[LedgerPermission]]:
    """A ledger guarded in production by the real registry, which knows only `prod-token`."""
    production = {"prod-token": Identity.user("ana", permissions=[LedgerPermission.POST])}
    auth = AccessControl[LedgerPermission](providers=[RegistryProvider(production)])
    ledger = UseFramework("ledger-replacing", log_after_execution=False)

    @ledger.feature(CommandPostEntry)
    @auth.requires(LedgerPermission.POST)
    class PostEntry(Feature):
        def execute(self, dto: CommandPostEntry) -> ResponsePosted:
            return ResponsePosted(by=security.identity.subject)

    auth.on(ledger)
    return ledger, auth


def test_a_fresh_bus_authenticates_with_the_providers_the_test_gives_it():
    ledger, auth = guarded()
    test = ledger.fresh()
    fake = StaticProvider(
        {"test-token": Identity.user("tester", permissions=[LedgerPermission.POST])}
    )

    test_auth = auth.using(providers=[fake])
    test_auth.on(test, replaces=auth)

    assert AccessControl.of(test) is test_auth and AccessControl.of(ledger) is auth
    with security.authenticated_as(test, bearer("test-token")):
        posted = test(CommandPostEntry(amount=10), ResponsePosted)
    assert posted.by == "tester"
    with pytest.raises(exceptions.Unauthenticated):
        with security.authenticated_as(test, bearer("prod-token")):
            pass


def test_the_rules_of_production_still_hold_on_the_fresh_bus():
    ledger, auth = guarded()
    test = ledger.fresh()
    fake = StaticProvider({"no-rights": Identity.user("guest")})
    auth.using(providers=[fake]).on(test, replaces=auth)

    with pytest.raises(exceptions.PermissionDenied):
        with security.authenticated_as(test, bearer("no-rights")):
            test(CommandPostEntry(amount=10))


def test_production_keeps_its_own_providers():
    ledger, auth = guarded()
    test = ledger.fresh()
    auth.using(providers=[StaticProvider({})]).on(test, replaces=auth)

    with security.authenticated_as(ledger, bearer("prod-token")):
        assert ledger(CommandPostEntry(amount=1), ResponsePosted).by == "ana"


def test_a_fresh_bus_made_of_the_test_bus_keeps_the_replacement():
    ledger, auth = guarded()
    test = ledger.fresh()
    test_auth = auth.using(
        providers=[
            StaticProvider(
                {"t": Identity.user("tester", permissions=[LedgerPermission.POST])}
            )
        ]
    )
    test_auth.on(test, replaces=auth)

    again = test.fresh()

    assert AccessControl.of(again) is test_auth
    with security.authenticated_as(again, bearer("t")):
        assert again(CommandPostEntry(amount=1), ResponsePosted).by == "tester"


def test_replacing_what_does_not_guard_the_bus_says_so():
    _, auth = guarded()
    other = UseFramework("ledger-unguarded", log_after_execution=False)

    with pytest.raises(ProgrammingError, match="does not guard 'ledger-unguarded'"):
        auth.using(providers=[]).on(other, replaces=auth)


# --- a backend that does not answer ---------------------------------------------------------------


class DownRegistryProvider(RegistryProvider):
    """The registry provider with its registry down."""

    def authenticate(self, credentials: Credentials) -> Identity | None:
        if credentials.bearer is None:
            return None
        raise ServiceUnavailableError("the registry did not answer")


def asked(middleware: IdentityMiddleware, token: str) -> list[dict[str, Any]]:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "query_string": b"",
    }
    sent: list[dict[str, Any]] = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    return sent


async def ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_a_provider_whose_backend_is_down_is_a_503_to_retry():
    access = AccessControl(providers=[DownRegistryProvider({})])

    start, body = asked(IdentityMiddleware(ok_app, access), "any")

    assert start["status"] == 503 and (b"retry-after", b"1") in start["headers"]
    assert json.loads(body["body"])["error"]["kind"] == "unavailable"


def test_failed_credentials_are_still_a_401():
    access = AccessControl(providers=[RegistryProvider({})])

    start, _ = asked(IdentityMiddleware(ok_app, access), "unknown")

    assert start["status"] == 401


class TestRegistryProviderContract(AuthProviderContract):
    """The contract every provider owes, its backend down included."""

    def make_provider(self) -> AuthProvider:
        return RegistryProvider({"good": Identity.user("ana")})

    def accepted(self) -> Credentials:
        return bearer("good")

    def foreign(self) -> Credentials:
        return Credentials(transport="http", headers={"x-api-key": "k"})

    def rejected(self) -> Credentials:
        return bearer("bad")

    def make_provider_whose_backend_is_down(self) -> AuthProvider | None:
        return DownRegistryProvider({})
