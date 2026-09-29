"""One failure, one kind, on every wire — each answering with its own code — and a catalog
computed once, not on every request.

The incidents: a domain refusal answered as "Internal error", so a client could not tell "the
invoice does not balance" from "the server fell over"; a stale write answered like any other
refusal, so a client could not know to read again and retry; every JSON-RPC request recomputing
every use case's schema.
"""

from collections.abc import Iterator
from typing import Any

import grpc
import pytest
from google.protobuf.struct_pb2 import Struct
from starlette.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd.exceptions import DomainError, DuplicateAggregate, StaleAggregate
from sincpro_framework.entrypoints import json_utils
from sincpro_framework.entrypoints.errors import FailureKind, failure_kind
from sincpro_framework.entrypoints.grpc import GrpcGateway
from sincpro_framework.entrypoints.grpc.wire import scalar_to_struct
from sincpro_framework.entrypoints.rest import RestGateway
from sincpro_framework.entrypoints.rpc import RpcGateway
from sincpro_framework.entrypoints.rpc.jrpc import CONFLICT, DOMAIN_ERROR, INTERNAL_ERROR

RAISED = {
    "domain": DomainError("an invoice has to balance"),
    "stale": StaleAggregate("invoice F-1 was written by someone else"),
    "duplicate": DuplicateAggregate("invoice F-1 already exists"),
    "internal": RuntimeError("postgres://admin:hunter2@db"),
}


class CommandFail(DataTransferObject):
    how: str


def _billing() -> UseFramework:
    billing = UseFramework("failing-billing", log_after_execution=False)

    @billing.feature(CommandFail)
    class Fail(Feature):
        def execute(self, dto: CommandFail) -> None:
            raise RAISED[dto.how]

    return billing


def test_each_failure_is_one_kind() -> None:
    assert [failure_kind(one) for one in RAISED.values()] == [
        FailureKind.DOMAIN,
        FailureKind.CONFLICT,
        FailureKind.CONFLICT,
        FailureKind.INTERNAL,
    ]


@pytest.mark.parametrize(
    ("how", "code", "disclosed"),
    [
        ("domain", DOMAIN_ERROR, "an invoice has to balance"),
        ("stale", CONFLICT, "invoice F-1 was written by someone else"),
        ("duplicate", CONFLICT, "invoice F-1 already exists"),
        ("internal", INTERNAL_ERROR, None),
    ],
)
def test_json_rpc_answers_each_kind_with_its_code(
    how: str, code: int, disclosed: Any
) -> None:
    rpc = RpcGateway({"billing": _billing()})
    answered = rpc.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "billing.features.CommandFail",
            "params": {"how": how},
        }
    )
    assert isinstance(answered, dict)
    assert answered["error"]["code"] == code
    assert answered["error"].get("data") == disclosed


@pytest.fixture
def grpc_call() -> Iterator[Any]:
    server = GrpcGateway({"billing": _billing()}).server(max_workers=2)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    call = channel.unary_unary(
        "/billing.Features/CommandFail",
        request_serializer=Struct.SerializeToString,
        response_deserializer=Struct.FromString,
    )
    yield call
    channel.close()
    server.stop(None)


@pytest.mark.parametrize(
    ("how", "code"),
    [
        ("domain", grpc.StatusCode.FAILED_PRECONDITION),
        ("stale", grpc.StatusCode.ABORTED),
        ("duplicate", grpc.StatusCode.ALREADY_EXISTS),
        ("internal", grpc.StatusCode.INTERNAL),
    ],
)
def test_grpc_answers_each_kind_with_its_code(grpc_call: Any, how: str, code: Any) -> None:
    with pytest.raises(grpc.RpcError) as failed:
        grpc_call(scalar_to_struct({"how": how}))
    refused: Any = failed.value
    assert refused.code() == code
    assert "hunter2" not in refused.details()


@pytest.mark.parametrize(
    ("how", "status", "kind"),
    [
        ("domain", 422, "domain"),
        ("stale", 409, "conflict"),
        ("duplicate", 409, "conflict"),
        ("internal", 500, "internal"),
    ],
)
def test_rest_answers_each_kind_with_its_status(how: str, status: int, kind: str) -> None:
    client = TestClient(RestGateway({"billing": _billing()}).app())
    answered = client.post("/billing/command-fail", json={"how": how})
    assert (answered.status_code, answered.json()["error"]["kind"]) == (status, kind)


def test_the_catalog_is_computed_once_not_on_every_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    computed: list[type] = []
    schema_of = json_utils.dto_json_schema

    def counting(dto_type: Any) -> dict[str, Any]:
        computed.append(dto_type)
        return schema_of(dto_type)

    monkeypatch.setattr(json_utils, "dto_json_schema", counting)
    rpc = RpcGateway({"billing": _billing()})
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "billing.features.CommandFail",
        "params": {"how": "domain"},
    }
    for _ in range(5):
        rpc.handle(call)
    assert computed == [CommandFail]


def test_narrowing_a_catalog_lets_what_it_kept_go() -> None:
    rpc = RpcGateway({"billing": _billing()})
    assert "billing.features.CommandFail" in rpc.methods()
    rpc.catalogs["billing"].exclude(CommandFail)
    assert "billing.features.CommandFail" not in rpc.methods()
