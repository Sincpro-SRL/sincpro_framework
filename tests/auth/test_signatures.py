"""HMAC signatures over the body, written once for both sides: GitHub's documented example
verifies, a signed moment outside its tolerance or a replay is refused, a sender rotates its
secret, and every wire hands a provider the body as it came — the app still reading it whole.
"""

import asyncio
import json

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    AuthProvider,
    Credentials,
    HexDigest,
    IdentityKind,
    IdentityMiddleware,
    OutgoingRequest,
    Permission,
    RequestSigner,
    SignatureProvider,
    TimestampedSignature,
    security,
)
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.entrypoints import RpcGateway
from sincpro_framework.entrypoints.domain.bindings import RpcBinding
from sincpro_framework.runtime.testing import AuthProviderContract

SECRET = "a webhook secret"
NOW = 1_900_000_000


class P(Permission):
    NOTIFY = "payments.payment.notify"


def test_githubs_documented_example_verifies() -> None:
    style = HexDigest()
    value = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
    assert style.matches(b"It's a Secret to Everybody", b"Hello, World!", value)
    assert not style.matches(b"another secret", b"Hello, World!", value)


def webhook(**options) -> SignatureProvider:
    return SignatureProvider(
        {"2026-09": SECRET},
        TimestampedSignature("x-gateway-signature"),
        subject="service:gateway",
        permissions={P.NOTIFY},
        clock=lambda: NOW,
        **options,
    )


def signed(body: bytes, secret: str = SECRET, at: int = NOW) -> Credentials:
    header = RequestSigner(
        secret, TimestampedSignature("x-gateway-signature"), clock=lambda: at
    )
    return Credentials(
        transport="http",
        headers=header.headers_for(OutgoingRequest(url="https://billing/x", body=body)),
        body=body,
    )


def test_what_the_signer_signs_the_provider_verifies() -> None:
    identity = webhook().authenticate(signed(b'{"paid": 10}'))
    assert identity is not None
    assert (identity.subject, identity.kind, identity.permissions) == (
        "service:gateway",
        IdentityKind.SERVICE,
        {P.NOTIFY},
    )


@pytest.mark.parametrize(
    ("credentials", "reason"),
    [
        (signed(b"{}", secret="not the secret"), "does not match"),
        (signed(b"{}", at=NOW - 301), "outside its tolerance"),
        (signed(b"{}", at=NOW + 301), "outside its tolerance"),
    ],
)
def test_a_wrong_or_stale_signature_is_refused(credentials: Credentials, reason: str) -> None:
    with pytest.raises(exceptions.Unauthenticated, match=reason):
        webhook().authenticate(credentials)


def test_a_body_changed_after_signing_is_refused() -> None:
    credentials = signed(b'{"paid": 10}').model_copy(update={"body": b'{"paid": 99}'})
    with pytest.raises(exceptions.Unauthenticated, match="does not match"):
        webhook().authenticate(credentials)


def test_a_signature_is_accepted_once_within_its_tolerance() -> None:
    provider = webhook(replay=InMemoryKeyValue())
    credentials = signed(b'{"paid": 10}')
    assert provider.authenticate(credentials) is not None
    with pytest.raises(exceptions.Unauthenticated, match="already used"):
        provider.authenticate(credentials)


def test_a_sender_rotating_its_secret_is_accepted_with_either() -> None:
    provider = SignatureProvider(
        {"old": "old secret", "new": "new secret"}, HexDigest(), subject="service:github"
    )
    for secret in ("old secret", "new secret"):
        headers = RequestSigner(secret, HexDigest()).headers_for(
            OutgoingRequest(url="x", body=b"b")
        )
        assert provider.authenticate(
            Credentials(transport="http", headers=headers, body=b"b")
        )


def test_a_wire_that_hands_no_body_is_a_programming_error() -> None:
    credentials = signed(b"{}").model_copy(update={"body": None})
    with pytest.raises(exceptions.ProgrammingError, match="handed none"):
        webhook().authenticate(credentials)


class CommandNotify(DataTransferObject):
    paid: int = 0


class Notified(DataTransferObject):
    by: str
    paid: int


def guarded() -> tuple[UseFramework, AccessControl[P]]:
    auth = AccessControl[P](providers=[webhook()])
    payments = UseFramework("signed-payments", log_after_execution=False)

    @payments.feature(CommandNotify)
    @auth.requires(P.NOTIFY)
    class Notify(Feature):
        def execute(self, dto: CommandNotify) -> Notified:
            return Notified(by=security.identity.subject, paid=dto.paid)

    auth.on(payments)
    return payments, auth


def test_a_project_app_verifies_the_body_and_still_reads_it_whole() -> None:
    payments, auth = guarded()

    async def notify(request: Request) -> JSONResponse:
        paid = json.loads(await request.body())["paid"]
        answer = await asyncio.to_thread(payments, CommandNotify(paid=paid), Notified)
        assert answer is not None
        return JSONResponse(answer.model_dump())

    app = Starlette(routes=[Route("/notify", notify, methods=["POST"])])
    app.add_middleware(IdentityMiddleware, access=auth)
    client = TestClient(app)
    body = b'{"paid": 10}'
    headers = dict(signed(body).headers)
    assert client.post("/notify", content=body, headers=headers).json() == {
        "by": "service:gateway",
        "paid": 10,
    }
    assert client.post("/notify", content=b'{"paid": 99}', headers=headers).status_code == 401


def test_json_rpc_hands_the_body_as_it_came() -> None:
    payments, _ = guarded()
    gateway = RpcGateway({"payments": payments}).bind(CommandNotify, RpcBinding())
    client = TestClient(gateway.app())
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "payments.notify", "params": {"paid": 5}}
    ).encode()
    headers = {**signed(body).headers, "content-type": "application/json"}
    assert client.post("/rpc", content=body, headers=headers).json()["result"] == {
        "by": "service:gateway",
        "paid": 5,
    }


class TestSignatureProviderContract(AuthProviderContract):
    def make_provider(self) -> AuthProvider:
        return webhook()

    def accepted(self) -> Credentials:
        return signed(b"{}")

    def foreign(self) -> Credentials:
        return Credentials(
            transport="http", headers={"authorization": "Bearer t"}, body=b"{}"
        )

    def rejected(self) -> Credentials:
        return signed(b"{}", secret="forged")

    def granted_permission(self) -> str | None:
        return P.NOTIFY


def test_a_signer_never_shows_its_secret() -> None:
    assert SECRET not in repr(RequestSigner(SECRET, HexDigest()))
