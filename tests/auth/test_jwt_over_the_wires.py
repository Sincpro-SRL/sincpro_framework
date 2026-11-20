"""One `JwtProvider` behind the framework's own wires — FastAPI and gRPC: a good token runs the
use case as its subject, an expired one is refused before it, and the refusal carries the
provider's own challenge (`error="invalid_token"`), not a bare `Bearer`."""

from collections.abc import Iterator
from typing import Any

import grpc
import pytest
from fastapi.testclient import TestClient
from google.protobuf.struct_pb2 import Struct

from sincpro_framework import DataTransferObject, Feature, UseFramework, entrypoints
from sincpro_framework.auth import AccessControl, JwtProvider, RolePermissions, security
from sincpro_framework.entrypoints import FastApiGateway, GrpcGateway
from sincpro_framework.entrypoints.adapters.grpc.wire import (
    scalar_to_struct,
    struct_to_scalar,
)
from sincpro_framework.entrypoints.entrypoint.decorators import rest

from .test_jwt_provider import NOW, Perm, claims, issuer, mint

GOOD = mint(claims())
EXPIRED = mint(claims(exp=NOW - 120))


class CommandIssueInvoice(DataTransferObject):
    total: int


class Issued(DataTransferObject):
    number: str
    by: str


def _billing() -> UseFramework:
    """A billing context whose one command needs `Perm.ISSUE`, granted to Keycloak's
    `accountant` client role."""
    auth = AccessControl[Perm](
        providers=[
            JwtProvider(issuer(), resolvers=[RolePermissions({"accountant": {Perm.ISSUE}})])
        ]
    )
    bus = UseFramework("billing", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @auth.requires(Perm.ISSUE)
    @rest.post("/invoices", status=201)
    @entrypoints.grpc()
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Issued:
            return Issued(number=f"F-{dto.total}", by=security.identity.subject)

    auth.on(bus)
    return bus


def test_fastapi_runs_a_good_token_and_refuses_an_expired_one_with_its_challenge() -> None:
    api = FastApiGateway([_billing()])
    client = TestClient(api.app())
    ok = client.post(
        "/billing/invoices", json={"total": 7}, headers={"authorization": f"Bearer {GOOD}"}
    )
    assert ok.status_code == 201, ok.text
    assert ok.json() == {"number": "F-7", "by": "user:4c1d"}
    expired = client.post(
        "/billing/invoices", json={"total": 7}, headers={"authorization": f"Bearer {EXPIRED}"}
    )
    assert expired.status_code == 401
    challenge = expired.headers["www-authenticate"]
    assert 'error="invalid_token"' in challenge and "expired" in challenge
    nobody = client.post("/billing/invoices", json={"total": 7})
    assert nobody.status_code == 401
    assert "invalid_token" not in nobody.headers["www-authenticate"]


@pytest.fixture
def channel() -> Iterator[grpc.Channel]:
    server = GrpcGateway({"billing": _billing()}).server(max_workers=2, reflection=False)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    opened = grpc.insecure_channel(f"127.0.0.1:{port}")
    yield opened
    opened.close()
    server.stop(None)


def _issue(channel: grpc.Channel, token: str) -> Any:
    """The answer, or the code and the trailing `www-authenticate` of the refusal."""
    method = channel.unary_unary(
        "/billing.v1.BillingService/IssueInvoice",
        request_serializer=Struct.SerializeToString,
        response_deserializer=Struct.FromString,
    )
    try:
        answered = method(
            scalar_to_struct({"total": 7}), metadata=(("authorization", f"Bearer {token}"),)
        )
        return struct_to_scalar(answered)
    except grpc.RpcError as failed:
        error: Any = failed
        trailing = dict(error.trailing_metadata() or ())
        return error.code(), trailing.get("www-authenticate", "")


def test_grpc_runs_a_good_token_and_refuses_an_expired_one_with_its_challenge(
    channel: grpc.Channel,
) -> None:
    assert _issue(channel, GOOD) == {"number": "F-7", "by": "user:4c1d"}
    code, challenge = _issue(channel, EXPIRED)
    assert code == grpc.StatusCode.UNAUTHENTICATED
    assert 'error="invalid_token"' in challenge and "expired" in challenge
