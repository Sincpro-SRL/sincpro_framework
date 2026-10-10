"""The JSON-RPC 2.0 specification's own examples, answered as the specification answers them.

The incidents these protect: a batch of notifications answered with an empty array a client
had to special-case, a notification that failed answered anyway, an invalid request object
without an `id` silently dropped as if it were a notification, and a bus aliased `rpc`
publishing methods under the prefix the specification reserves for the protocol itself.
Every use case here is declared with `@rpc()` — the gateway's default, declared exposure.

Every example of https://www.jsonrpc.org/specification §7 is here verbatim. Two things are
translated, both documented in docs/entrypoints/rpc.md:

- method names: the specification's `subtract` is this gateway's `spec.subtract` — the
  namespace (the bus's alias) and the DTO's name in snake_case (PRD_15 §4);
- by-position params: this wire is by-name only, so each positional example is sent twice —
  verbatim (answered `-32602 Invalid params`), and with the same values by name (answered as
  the specification answers).

A result is always the declared response rendered as an object: a Feature returning `19`
answers `{"result": 19}`, so the specification's `"result": 19` is `"result": {"result": 19}`.
"""

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, ProgrammingError, UseFramework
from sincpro_framework.entrypoints.adapters.rpc import RpcGateway
from sincpro_framework.entrypoints.adapters.rpc.errors import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
)
from sincpro_framework.entrypoints.entrypoint.decorators import rpc

EXAMPLES: list[tuple[str, str, str | None]] = [
    (
        "positional-1",
        '{"jsonrpc": "2.0", "method": "subtract", "params": [42, 23], "id": 1}',
        '{"jsonrpc": "2.0", "result": 19, "id": 1}',
    ),
    (
        "positional-2",
        '{"jsonrpc": "2.0", "method": "subtract", "params": [23, 42], "id": 2}',
        '{"jsonrpc": "2.0", "result": -19, "id": 2}',
    ),
    (
        "named-1",
        '{"jsonrpc": "2.0", "method": "subtract", "params": {"subtrahend": 23, "minuend": 42},'
        ' "id": 3}',
        '{"jsonrpc": "2.0", "result": 19, "id": 3}',
    ),
    (
        "named-2",
        '{"jsonrpc": "2.0", "method": "subtract", "params": {"minuend": 42, "subtrahend": 23},'
        ' "id": 4}',
        '{"jsonrpc": "2.0", "result": 19, "id": 4}',
    ),
    (
        "notification-update",
        '{"jsonrpc": "2.0", "method": "update", "params": [1,2,3,4,5]}',
        None,
    ),
    ("notification-foobar", '{"jsonrpc": "2.0", "method": "foobar"}', None),
    (
        "non-existent-method",
        '{"jsonrpc": "2.0", "method": "foobar", "id": "1"}',
        '{"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": "1"}',
    ),
    (
        "invalid-json",
        '{"jsonrpc": "2.0", "method": "foobar, "params": "bar", "baz]',
        '{"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": null}',
    ),
    (
        "invalid-request-object",
        '{"jsonrpc": "2.0", "method": 1, "params": "bar"}',
        '{"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null}',
    ),
    (
        "batch-invalid-json",
        '[{"jsonrpc": "2.0", "method": "sum", "params": [1,2,4], "id": "1"},'
        ' {"jsonrpc": "2.0", "method"]',
        '{"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": null}',
    ),
    (
        "empty-array",
        "[]",
        '{"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null}',
    ),
    (
        "invalid-batch-not-empty",
        "[1]",
        '[{"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"},'
        ' "id": null}]',
    ),
    (
        "invalid-batch",
        "[1,2,3]",
        "["
        '{"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null},'
        '{"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null},'
        '{"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null}'
        "]",
    ),
    (
        "batch",
        "["
        '{"jsonrpc": "2.0", "method": "sum", "params": [1,2,4], "id": "1"},'
        '{"jsonrpc": "2.0", "method": "notify_hello", "params": [7]},'
        '{"jsonrpc": "2.0", "method": "subtract", "params": [42,23], "id": "2"},'
        '{"foo": "boo"},'
        '{"jsonrpc": "2.0", "method": "foo.get", "params": {"name": "myself"}, "id": "5"},'
        '{"jsonrpc": "2.0", "method": "get_data", "id": "9"}'
        "]",
        "["
        '{"jsonrpc": "2.0", "result": 7, "id": "1"},'
        '{"jsonrpc": "2.0", "result": 19, "id": "2"},'
        '{"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null},'
        '{"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": "5"},'
        '{"jsonrpc": "2.0", "result": ["hello", 5], "id": "9"}'
        "]",
    ),
    (
        "batch-all-notifications",
        "["
        '{"jsonrpc": "2.0", "method": "notify_sum", "params": [1,2,4]},'
        '{"jsonrpc": "2.0", "method": "notify_hello", "params": [7]}'
        "]",
        None,
    ),
]


class Subtract(DataTransferObject):
    minuend: int
    subtrahend: int


class Sum(DataTransferObject):
    values: list[int]


class Update(DataTransferObject):
    values: list[int]


class NotifyHello(DataTransferObject):
    value: int


class NotifySum(DataTransferObject):
    values: list[int]


class GetData(DataTransferObject):
    pass


class Refuse(DataTransferObject):
    pass


NAMES = {
    spec_name: f"spec.{spec_name}"
    for spec_name in ("subtract", "sum", "update", "notify_hello", "notify_sum", "get_data")
}
"""The specification's method names are this gateway's operations, under the namespace `spec`."""

BY_NAME: dict[str, Callable[[list[Any]], dict[str, Any]]] = {
    "subtract": lambda values: {"minuend": values[0], "subtrahend": values[1]},
    "sum": lambda values: {"values": values},
    "update": lambda values: {"values": values},
    "notify_hello": lambda values: {"value": values[0]},
    "notify_sum": lambda values: {"values": values},
}


def _spec_bus(ran: list[tuple[str, Any]]) -> UseFramework:
    spec = UseFramework("jsonrpc-spec", log_after_execution=False)

    @spec.feature(Subtract)
    @rpc()
    class SubtractNumbers(Feature):
        def execute(self, dto: Subtract) -> int:
            ran.append(("subtract", dto.minuend - dto.subtrahend))
            return dto.minuend - dto.subtrahend

    @spec.feature(Sum)
    @rpc()
    class SumNumbers(Feature):
        def execute(self, dto: Sum) -> int:
            ran.append(("sum", sum(dto.values)))
            return sum(dto.values)

    @spec.feature(Update)
    @rpc()
    class UpdateValues(Feature):
        def execute(self, dto: Update) -> None:
            ran.append(("update", dto.values))

    @spec.feature(NotifyHello)
    @rpc()
    class Hello(Feature):
        def execute(self, dto: NotifyHello) -> None:
            ran.append(("notify_hello", dto.value))

    @spec.feature(NotifySum)
    @rpc()
    class NotifyTotal(Feature):
        def execute(self, dto: NotifySum) -> None:
            ran.append(("notify_sum", sum(dto.values)))

    @spec.feature(GetData)
    @rpc()
    class Data(Feature):
        def execute(self, dto: GetData) -> list[Any]:
            return ["hello", 5]

    @spec.feature(Refuse)
    @rpc()
    class Refuses(Feature):
        def execute(self, dto: Refuse) -> None:
            ran.append(("refuse", None))
            raise ProgrammingError("never")

    spec.build_root_bus()
    return spec


@pytest.fixture
def ran() -> list[tuple[str, Any]]:
    return []


@pytest.fixture
def gateway(ran: list[tuple[str, Any]]) -> RpcGateway:
    return RpcGateway({"spec": _spec_bus(ran)}, unguarded=True)


@pytest.fixture
def client(gateway: RpcGateway) -> Iterator[TestClient]:
    with TestClient(gateway.app()) as client:
        yield client


def _translated(one: Any, by_name: bool) -> Any:
    if not isinstance(one, dict) or not isinstance(one.get("method"), str):
        return one
    spec_name = one["method"]
    renamed = {**one, "method": NAMES.get(spec_name, spec_name)}
    if by_name and isinstance(one.get("params"), list) and spec_name in BY_NAME:
        renamed["params"] = BY_NAME[spec_name](one["params"])
    return renamed


def _request_body(text: str, by_name: bool) -> str:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, list):
        return json.dumps([_translated(one, by_name) for one in parsed])
    return json.dumps(_translated(parsed, by_name))


def _normalized(reply: dict[str, Any]) -> dict[str, Any]:
    """The reply as the specification writes it: `data` checked, then set aside (it MAY be
    omitted, so the specification never shows it), and a result unwrapped from its object."""
    if "error" in reply:
        data = reply["error"].get("data")
        assert isinstance(data, dict)
        assert {"kind", "reason", "retryable"} <= set(data)
        error = {key: reply["error"][key] for key in ("code", "message")}
        return {"jsonrpc": reply["jsonrpc"], "error": error, "id": reply["id"]}
    assert set(reply["result"]) == {"result"}
    return {
        "jsonrpc": reply["jsonrpc"],
        "result": reply["result"]["result"],
        "id": reply["id"],
    }


INVALID_PARAMS_ERROR = '{"code": -32602, "message": "Invalid params"}'
BY_POSITION_ANSWERS = {
    "positional-1": f'{{"jsonrpc": "2.0", "error": {INVALID_PARAMS_ERROR}, "id": 1}}',
    "positional-2": f'{{"jsonrpc": "2.0", "error": {INVALID_PARAMS_ERROR}, "id": 2}}',
    "batch": "["
    f'{{"jsonrpc": "2.0", "error": {INVALID_PARAMS_ERROR}, "id": "1"}},'
    f'{{"jsonrpc": "2.0", "error": {INVALID_PARAMS_ERROR}, "id": "2"}},'
    '{"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request"}, "id": null},'
    '{"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": "5"},'
    '{"jsonrpc": "2.0", "result": ["hello", 5], "id": "9"}'
    "]",
}
"""The verbatim answer where it differs from the specification's: a call by position is
`-32602`. A positional notification is still answered nothing, so it needs no entry."""


def _sorted(replies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(replies, key=lambda one: json.dumps(one, sort_keys=True))


@pytest.mark.parametrize("by_name", [True, False], ids=["by-name", "verbatim"])
@pytest.mark.parametrize(
    ("name", "request_text", "response_text"),
    EXAMPLES,
    ids=[name for name, _, _ in EXAMPLES],
)
def test_every_example_of_the_specification_is_answered_as_it_answers(
    client: TestClient,
    name: str,
    request_text: str,
    response_text: str | None,
    by_name: bool,
) -> None:
    answered = client.post(
        "/rpc",
        content=_request_body(request_text, by_name),
        headers={"content-type": "application/json"},
    )
    if not by_name:
        response_text = BY_POSITION_ANSWERS.get(name, response_text)
    expected = json.loads(response_text) if response_text is not None else None

    if expected is None:
        assert (answered.status_code, answered.content) == (204, b"")
        return
    assert answered.status_code == 200
    body = answered.json()
    if isinstance(expected, list):
        # Calls in a batch may run and answer in any order (JSON-RPC 2.0 §6).
        assert isinstance(body, list)
        assert _sorted([_normalized(one) for one in body]) == _sorted(expected)
    else:
        assert _normalized(body) == expected


def test_a_notification_runs_the_use_case_and_answers_nothing(
    client: TestClient, ran: list[tuple[str, Any]]
) -> None:
    body = {"jsonrpc": "2.0", "method": "spec.update", "params": {"values": [1, 2]}}

    answered = client.post("/rpc", json=body)

    assert (answered.status_code, answered.content) == (204, b"")
    assert ran == [("update", [1, 2])]


@pytest.mark.parametrize(
    "notification",
    [
        {"jsonrpc": "2.0", "method": "spec.nothing"},
        {"jsonrpc": "2.0", "method": "spec.subtract", "params": {"minuend": 1}},
        {"jsonrpc": "2.0", "method": "spec.subtract", "params": [1, 2]},
        {"jsonrpc": "2.0", "method": "spec.refuse", "params": {}},
    ],
    ids=["unknown-method", "invalid-params", "positional", "domain-refusal"],
)
def test_a_notification_that_fails_is_never_answered(
    client: TestClient, notification: dict[str, Any]
) -> None:
    answered = client.post("/rpc", json=notification)

    assert (answered.status_code, answered.content) == (204, b"")
    assert client.post("/rpc", json=[notification, notification]).status_code == 204


def test_an_invalid_request_without_an_id_is_answered_not_dropped(
    gateway: RpcGateway,
) -> None:
    """Not a notification: a notification is a valid Request object without an `id`."""
    for invalid in (
        {"jsonrpc": "1.0", "method": "spec.get_data"},
        {"jsonrpc": "2.0", "method": 1},
        {"jsonrpc": "2.0", "method": "spec.get_data", "params": "bar"},
        {"jsonrpc": "2.0", "method": "spec.get_data", "context": "not-an-object"},
    ):
        answered = gateway.handle(invalid)
        assert isinstance(answered, dict)
        assert (answered["error"]["code"], answered["id"]) == (INVALID_REQUEST, None)


def test_an_id_null_is_a_call_and_is_answered_with_id_null(gateway: RpcGateway) -> None:
    answered = gateway.handle({"jsonrpc": "2.0", "method": "spec.get_data", "id": None})

    assert answered == {"jsonrpc": "2.0", "id": None, "result": {"result": ["hello", 5]}}


@pytest.mark.parametrize("wrong_id", [{"a": 1}, [1], True])
def test_an_id_that_is_not_a_string_number_or_null_is_an_invalid_request(
    gateway: RpcGateway, ran: list[tuple[str, Any]], wrong_id: Any
) -> None:
    answered = gateway.handle(
        {
            "jsonrpc": "2.0",
            "method": "spec.update",
            "params": {"values": []},
            "id": wrong_id,
        }
    )

    assert isinstance(answered, dict)
    assert (answered["error"]["code"], answered["id"]) == (INVALID_REQUEST, None)
    assert ran == []


def test_each_invalid_item_of_a_batch_gets_its_own_error(gateway: RpcGateway) -> None:
    answered = gateway.handle(
        [
            {"jsonrpc": "2.0", "method": "spec.get_data", "id": 1},
            {"jsonrpc": "2.0", "method": "spec.nothing", "id": 2},
            {"jsonrpc": "2.0", "method": "spec.subtract", "params": {}, "id": 3},
            "not-a-request",
        ]
    )

    assert isinstance(answered, list)
    by_id = {(one["id"], one.get("error", {}).get("code")) for one in answered}
    assert by_id == {
        (1, None),
        (2, METHOD_NOT_FOUND),
        (3, INVALID_PARAMS),
        (None, INVALID_REQUEST),
    }


def test_the_rpc_prefix_is_reserved_for_discovery(gateway: RpcGateway) -> None:
    """A bus aliased `rpc` would publish `rpc.subtract` under the protocol's own prefix."""
    with pytest.raises(ValueError, match="rpc"):
        RpcGateway({"rpc": _spec_bus([])}, unguarded=True)

    unknown = gateway.handle({"jsonrpc": "2.0", "method": "rpc.anything", "id": 1})
    assert isinstance(unknown, dict)
    assert unknown["error"]["code"] == METHOD_NOT_FOUND
    discovered = gateway.handle({"jsonrpc": "2.0", "method": "rpc.discover", "id": 2})
    assert isinstance(discovered, dict)
    assert discovered["result"]["openrpc"].startswith("1.4.")
