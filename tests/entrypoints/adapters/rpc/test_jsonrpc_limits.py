"""A JSON-RPC endpoint is bounded before it runs anything.

The incident (PRD_15 §7, issue 6): a batch had no size limit, so one request of a hundred
thousand calls ran a hundred thousand use cases on the worker pool, and a body of any size was
read whole into memory before a byte of it was looked at.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.entrypoints.adapters.rpc import RpcGateway
from sincpro_framework.entrypoints.adapters.rpc.errors import INVALID_REQUEST
from sincpro_framework.entrypoints.entrypoint.decorators import rpc


class CommandCount(DataTransferObject):
    pass


def _counting(ran: list[int]) -> UseFramework:
    bus = UseFramework("limits", log_after_execution=False)

    @bus.feature(CommandCount)
    @rpc()
    class Count(Feature):
        def execute(self, dto: CommandCount) -> None:
            ran.append(1)

    bus.build_root_bus()
    return bus


def _call(request_id: int) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": "svc.count"}


@pytest.fixture
def ran() -> list[int]:
    return []


def test_a_batch_over_the_limit_answers_one_error_and_runs_nothing(ran: list[int]) -> None:
    gateway = RpcGateway({"svc": _counting(ran)}, unguarded=True)

    answered = gateway.handle([_call(one) for one in range(51)])

    assert isinstance(answered, dict)
    assert (answered["id"], answered["error"]["code"]) == (None, INVALID_REQUEST)
    data = answered["error"]["data"]
    assert (data["reason"], data["retryable"], data["limit"]) == (
        "BATCH_TOO_LARGE",
        False,
        50,
    )
    assert ran == []


def test_a_batch_at_the_limit_runs_every_call(ran: list[int]) -> None:
    gateway = RpcGateway({"svc": _counting(ran)}, unguarded=True)

    answered = gateway.handle([_call(one) for one in range(50)])

    assert isinstance(answered, list) and len(answered) == 50
    assert len(ran) == 50


def test_the_batch_limit_is_the_gateways_to_set(ran: list[int]) -> None:
    gateway = RpcGateway({"svc": _counting(ran)}, unguarded=True, max_batch_size=2)

    assert isinstance(gateway.handle([_call(1), _call(2)]), list)
    refused = gateway.handle([_call(1), _call(2), _call(3)])
    assert isinstance(refused, dict) and refused["error"]["data"]["limit"] == 2
    assert len(ran) == 2


@pytest.mark.parametrize(("batch", "body"), [(0, 1024), (50, -1)])
def test_a_limit_that_would_refuse_everything_is_refused_at_build(
    batch: int, body: int
) -> None:
    with pytest.raises(ValueError):
        RpcGateway(
            {"svc": _counting([])}, unguarded=True, max_batch_size=batch, max_body_bytes=body
        )


@pytest.fixture
def small_client(ran: list[int]) -> Iterator[TestClient]:
    with TestClient(
        RpcGateway({"svc": _counting(ran)}, unguarded=True, max_body_bytes=200).app()
    ) as client:
        yield client


def test_a_body_over_the_limit_is_413_and_runs_nothing(
    small_client: TestClient, ran: list[int]
) -> None:
    answered = small_client.post("/rpc", json=[_call(one) for one in range(5)])

    assert answered.status_code == 413
    assert answered.json()["error"]["data"]["reason"] == "BODY_TOO_LARGE"
    assert ran == []


def test_a_body_streamed_without_a_length_is_still_bounded(
    small_client: TestClient, ran: list[int]
) -> None:
    """A chunked body carries no `Content-Length` to refuse up front: it is counted as read."""

    def chunks() -> Iterator[bytes]:
        yield b"["
        for one in range(20):
            yield (b"," if one else b"") + b'{"jsonrpc":"2.0","method":"svc.count"}'
        yield b"]"

    answered = small_client.post(
        "/rpc", content=chunks(), headers={"content-type": "application/json"}
    )

    assert answered.status_code == 413
    assert ran == []


def test_the_default_body_limit_is_one_mebibyte(ran: list[int]) -> None:
    client = TestClient(RpcGateway({"svc": _counting(ran)}, unguarded=True).app())
    padding = "x" * (1024 * 1024)

    answered = client.post("/rpc", json={**_call(1), "context": {"padding": padding}})

    assert answered.status_code == 413


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/json; charset=utf-8", "application/json-rpc"],
)
def test_json_content_types_are_accepted(ran: list[int], content_type: str) -> None:
    client = TestClient(RpcGateway({"svc": _counting(ran)}, unguarded=True).app())

    answered = client.post(
        "/rpc",
        content=b'{"jsonrpc":"2.0","id":1,"method":"svc.count"}',
        headers={"content-type": content_type},
    )

    assert answered.status_code == 200 and answered.json()["result"] == {}


@pytest.mark.parametrize(
    "content_type", ["text/plain", "application/x-www-form-urlencoded", None]
)
def test_any_other_content_type_is_415_and_runs_nothing(
    ran: list[int], content_type: str | None
) -> None:
    client = TestClient(RpcGateway({"svc": _counting(ran)}, unguarded=True).app())
    headers = {"content-type": content_type} if content_type else {}

    answered = client.post(
        "/rpc",
        content=b'{"jsonrpc":"2.0","id":1,"method":"svc.count"}',
        headers=headers,
    )

    assert answered.status_code == 415
    assert answered.json()["error"]["data"]["reason"] == "UNSUPPORTED_MEDIA_TYPE"
    assert ran == []


def test_the_document_says_the_limits_it_enforces() -> None:
    document = RpcGateway(
        {"svc": _counting([])}, max_batch_size=7, max_body_bytes=900, unguarded=True
    ).discover()

    assert document["x-sincpro-limits"] == {"maxBatchSize": 7, "maxBodyBytes": 900}


def test_a_declared_length_over_the_limit_is_refused_before_the_body_is_read(
    ran: list[int],
) -> None:
    import asyncio

    app = RpcGateway({"svc": _counting(ran)}, unguarded=True, max_body_bytes=200).app()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/rpc",
        "raw_path": b"/rpc",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"content-length", b"999999")],
        "server": ("test", 80),
        "client": ("test", 1),
    }
    read: list[str] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        read.append("body")
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))

    assert sent[0]["status"] == 413
    assert read == []
