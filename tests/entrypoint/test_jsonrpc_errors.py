"""Every JSON-RPC error says what it is, whether to try again, and is published once.

The incidents: a client that could only switch on a number, and the numbers above -32000 mean
other things in other APIs; an idempotent run still in flight answered as a domain refusal, so
a client gave up on a call it only had to repeat; an OpenRPC document a generator refused,
because a nested DTO's `$ref` pointed at a `$defs` the document did not have.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth.domain import PermissionDenied, Unauthenticated
from sincpro_framework.caching.domain.exceptions import AlreadyInProgress, KeyReused
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    DuplicateAggregate,
    StaleAggregate,
)
from sincpro_framework.entrypoints.exposure import rpc
from sincpro_framework.entrypoints.rpc import RpcGateway
from sincpro_framework.entrypoints.rpc.errors import upper_snake

META_SCHEMA = Path(__file__).parent / "fixtures_openrpc_schema.json"

RAISED: dict[str, Exception] = {
    "domain": ContractViolation("an invoice has to balance"),
    "stale": StaleAggregate("invoice F-1 was written by someone else"),
    "duplicate": DuplicateAggregate("invoice F-1 already exists"),
    "in_progress": AlreadyInProgress("key k-1 is running"),
    "key_reused": KeyReused("key k-1 was used for another payload"),
    "unauthenticated": Unauthenticated("token expired"),
    "permission_denied": PermissionDenied("user:1", "invoices.issue"),
    "internal": RuntimeError("postgres://admin:hunter2@db"),
}


class CommandFail(DataTransferObject):
    how: str


class Line(DataTransferObject):
    sku: str


class CommandIssue(DataTransferObject):
    note: str = ""
    lines: list[Line]


class Issued(DataTransferObject):
    lines: list[Line]


def _billing() -> UseFramework:
    billing = UseFramework("rpc-errors-billing", log_after_execution=False)

    @billing.feature(CommandFail)
    @rpc()
    class Fail(Feature):
        def execute(self, dto: CommandFail) -> None:
            raise RAISED[dto.how]

    @billing.feature(CommandIssue)
    @rpc()
    class Issue(Feature):
        def execute(self, dto: CommandIssue) -> Issued:
            return Issued(lines=dto.lines)

    billing.build_root_bus()
    return billing


@pytest.fixture(scope="module")
def gateway() -> RpcGateway:
    return RpcGateway({"billing": _billing()}, unguarded=True)


def _error(gateway: RpcGateway, method: str, params: Any = None) -> dict[str, Any]:
    request: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        request["params"] = params
    answered = gateway.handle(request)
    assert isinstance(answered, dict)
    return answered["error"]


@pytest.mark.parametrize(
    ("how", "code", "kind", "reason", "retryable", "message"),
    [
        (
            "domain",
            -32010,
            "domain",
            "CONTRACT_VIOLATION",
            False,
            "an invoice has to balance",
        ),
        ("stale", -32009, "conflict", "STALE_AGGREGATE", True, str(RAISED["stale"])),
        (
            "duplicate",
            -32009,
            "conflict",
            "DUPLICATE_AGGREGATE",
            False,
            str(RAISED["duplicate"]),
        ),
        (
            "in_progress",
            -32029,
            "in_progress",
            "ALREADY_IN_PROGRESS",
            True,
            "key k-1 is running",
        ),
        ("key_reused", -32022, "key_reused", "KEY_REUSED", False, str(RAISED["key_reused"])),
        (
            "unauthenticated",
            -32001,
            "unauthenticated",
            "UNAUTHENTICATED",
            False,
            "token expired",
        ),
        (
            "permission_denied",
            -32003,
            "permission_denied",
            "PERMISSION_DENIED",
            False,
            "lacks invoices.issue",
        ),
    ],
)
def test_each_kind_answers_its_code_kind_reason_and_whether_to_retry(
    gateway: RpcGateway,
    how: str,
    code: int,
    kind: str,
    reason: str,
    retryable: bool,
    message: str,
) -> None:
    error = _error(gateway, "billing.fail", {"how": how})

    assert error["code"] == code
    data = error["data"]
    assert (data["kind"], data["reason"], data["retryable"]) == (kind, reason, retryable)
    assert data["message"] == message


def test_a_refusal_keeps_what_the_caller_comes_back_with(gateway: RpcGateway) -> None:
    error = _error(gateway, "billing.fail", {"how": "permission_denied"})

    assert error["data"]["requirement"] == "invoices.issue"
    assert error["data"]["message"] == "lacks invoices.issue"


def test_an_internal_failure_says_it_may_be_retried_and_nothing_of_the_inside(
    gateway: RpcGateway,
) -> None:
    error = _error(gateway, "billing.fail", {"how": "internal"})

    assert error == {
        "code": -32603,
        "message": "Internal error",
        "data": {"kind": "internal", "reason": "INTERNAL_ERROR", "retryable": True},
    }


def test_invalid_params_keep_the_validation_errors(gateway: RpcGateway) -> None:
    error = _error(gateway, "billing.fail", {})

    assert error["code"] == -32602
    assert error["data"]["reason"] == "VALIDATION_ERROR"
    assert error["data"]["errors"][0]["loc"] == ["how"]
    positional = _error(gateway, "billing.fail", ["domain"])
    assert (positional["code"], positional["data"]["reason"]) == (
        -32602,
        "PARAMS_BY_POSITION",
    )


def test_an_unknown_method_names_it(gateway: RpcGateway) -> None:
    error = _error(gateway, "billing.nope")

    assert (error["code"], error["data"]["method"]) == (-32601, "billing.nope")


def test_the_reason_is_the_error_class_in_upper_snake() -> None:
    assert upper_snake("ContractViolation") == "CONTRACT_VIOLATION"
    assert upper_snake("QRCodeExpired") == "QR_CODE_EXPIRED"
    assert upper_snake("KeyReused") == "KEY_REUSED"


# --- the document -------------------------------------------------------------------------

CATALOGUE = {-32602, -32001, -32003, -32004, -32009, -32029, -32022, -32010, -32000, -32603}
"""PRD_15 §1.3, the JSON-RPC column."""


def _resolve(document: dict[str, Any], pointer: str) -> Any:
    assert pointer.startswith("#/"), pointer
    found: Any = document
    for part in pointer[2:].split("/"):
        found = found[part.replace("~1", "/").replace("~0", "~")]
    return found


def _refs(node: Any) -> list[str]:
    if isinstance(node, dict):
        own = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
        return own + [ref for value in node.values() for ref in _refs(value)]
    if isinstance(node, list):
        return [ref for value in node for ref in _refs(value)]
    return []


def test_the_catalogue_is_published_once_and_referenced_by_every_method(
    gateway: RpcGateway,
) -> None:
    document = gateway.discover()
    published = document["components"]["errors"]

    codes = [one["code"] for one in published.values()]
    assert len(codes) == len(set(codes))
    assert set(codes) == CATALOGUE | {-32700, -32600, -32601}
    for method in document["methods"]:
        assert method["paramStructure"] == "by-name"
        if method["name"] == "rpc.discover":
            continue
        referenced = [_resolve(document, one["$ref"]) for one in method["errors"]]
        assert sorted(one["code"] for one in referenced) == sorted(CATALOGUE)


def test_every_ref_of_the_document_resolves_nested_dtos_included(gateway: RpcGateway) -> None:
    document = gateway.discover()

    refs = _refs(document)

    assert "#/components/schemas/Line" in refs
    for ref in refs:
        _resolve(document, ref)


def test_the_document_validates_against_the_openrpc_meta_schema(gateway: RpcGateway) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    vendored = json.loads(META_SCHEMA.read_text())
    json_schema_meta = referencing.Resource.from_contents(
        vendored["jsonSchemaMeta"], default_specification=referencing.jsonschema.DRAFT7
    )
    registry = referencing.Registry().with_resources(
        [
            ("https://meta.json-schema.tools/", json_schema_meta),
            ("https://meta.json-schema.tools", json_schema_meta),
        ]
    )
    validator = jsonschema.Draft7Validator(vendored["openrpc"], registry=registry)

    errors = [error.message for error in validator.iter_errors(gateway.discover())]

    assert errors == []


def test_the_body_context_is_published_as_an_extension_not_as_the_protocol(
    gateway: RpcGateway,
) -> None:
    assert gateway.discover()["x-sincpro-context"]["schema"]["type"] == "object"


def test_required_params_come_before_optional_ones(gateway: RpcGateway) -> None:
    """OpenRPC 1.4: every optional param MUST follow every required one — a DTO declares
    them in whatever order reads best."""
    for method in gateway.discover()["methods"]:
        flags = [param["required"] for param in method["params"]]
        assert flags == sorted(flags, reverse=True), method["name"]
