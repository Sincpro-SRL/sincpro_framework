"""Every gRPC refusal carries a `google.rpc.Status` a client switches on (PRD_15 §1.3, §3.3).

The incident: errors travelled as a text `details` only, so a client had to parse English to tell
"retry later" from "never retry", a validation error from a broken rule — and every generated
client in another language, which reads `grpc-status-details-bin`, found nothing at all.
"""

import json
from collections.abc import Iterator
from typing import Any

import grpc
import pytest
from google.protobuf.struct_pb2 import Struct
from google.rpc import error_details_pb2  # pyright: ignore[reportMissingImports]
from grpc_status import rpc_status  # pyright: ignore[reportMissingImports]

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth.domain.exceptions import PermissionDenied, Unauthenticated
from sincpro_framework.common.failures import FailureKind
from sincpro_framework.data_layer.caching import AlreadyInProgress, KeyReused
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    DomainError,
    DuplicateAggregate,
    StaleAggregate,
)
from sincpro_framework.entrypoints.adapters.grpc import GrpcGateway
from sincpro_framework.entrypoints.adapters.grpc.wire import scalar_to_struct
from sincpro_framework.entrypoints.domain.surface import Exposure


class InvoiceNotFound(DomainError):
    failure_kind = FailureKind.NOT_FOUND


class QuotaSpent(DomainError):
    failure_kind = FailureKind.EXHAUSTED
    retry_after = 7.0


class LedgerDown(Exception):
    """The inside of the process — its message is never told, only that it is worth a retry."""

    failure_kind = "unavailable"


RAISED: dict[str, Exception] = {
    "unauthenticated": Unauthenticated("token expired"),
    "permission_denied": PermissionDenied("user:1", "invoices:issue"),
    "domain": ContractViolation("an invoice has to balance"),
    "stale": StaleAggregate("invoice F-1 was written by someone else"),
    "duplicate": DuplicateAggregate("invoice F-1 already exists"),
    "not_found": InvoiceNotFound("invoice F-9 does not exist"),
    "in_progress": AlreadyInProgress("key k-1 is running"),
    "key_reused": KeyReused("key k-1 was used for another payload"),
    "exhausted": QuotaSpent("quota spent"),
    "unavailable": LedgerDown("postgres://admin:hunter2@ledger"),
    "internal": RuntimeError("postgres://admin:hunter2@db"),
}


class CommandFail(DataTransferObject):
    how: str
    amount: int = 0


def _billing() -> UseFramework:
    billing = UseFramework("rich-billing", log_after_execution=False)

    @billing.feature(CommandFail)
    class Fail(Feature):
        def execute(self, dto: CommandFail) -> None:
            raise RAISED[dto.how]

    return billing


@pytest.fixture(scope="module")
def call() -> Iterator[Any]:
    gateway = GrpcGateway({"billing": _billing()}, exposure=Exposure.CATALOG, unguarded=True)
    server = gateway.server(max_workers=2, reflection=False)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    yield channel.unary_unary(
        "/billing.v1.BillingService/Fail",
        request_serializer=Struct.SerializeToString,
        response_deserializer=Struct.FromString,
    )
    channel.close()
    server.stop(None)


def _refused(call: Any, payload: dict[str, Any]) -> tuple[Any, Any, dict[str, Any]]:
    with pytest.raises(grpc.RpcError) as failed:
        call(scalar_to_struct(payload))
    error: Any = failed.value
    status = rpc_status.from_call(error)
    assert status is not None, "no google.rpc.Status in grpc-status-details-bin"
    details: dict[str, Any] = {}
    for packed in status.details:
        for kind in (
            error_details_pb2.ErrorInfo,
            error_details_pb2.BadRequest,
            error_details_pb2.PreconditionFailure,
            error_details_pb2.RetryInfo,
        ):
            if packed.Is(kind.DESCRIPTOR):
                message = kind()
                packed.Unpack(message)
                details[kind.DESCRIPTOR.name] = message
    return error, status, details


@pytest.mark.parametrize(
    ("how", "code", "kind", "reason", "extra", "retry"),
    [
        (
            "unauthenticated",
            "UNAUTHENTICATED",
            "unauthenticated",
            "UNAUTHENTICATED",
            None,
            None,
        ),
        (
            "permission_denied",
            "PERMISSION_DENIED",
            "permission_denied",
            "PERMISSION_DENIED",
            None,
            None,
        ),
        (
            "domain",
            "FAILED_PRECONDITION",
            "domain",
            "CONTRACT_VIOLATION",
            "PreconditionFailure",
            None,
        ),
        ("stale", "ABORTED", "conflict", "STALE_AGGREGATE", None, None),
        ("duplicate", "ALREADY_EXISTS", "conflict", "DUPLICATE_AGGREGATE", None, None),
        ("not_found", "NOT_FOUND", "not_found", "INVOICE_NOT_FOUND", None, None),
        ("in_progress", "ABORTED", "in_progress", "ALREADY_IN_PROGRESS", None, 1),
        (
            "key_reused",
            "FAILED_PRECONDITION",
            "key_reused",
            "KEY_REUSED",
            "PreconditionFailure",
            None,
        ),
        ("exhausted", "RESOURCE_EXHAUSTED", "exhausted", "QUOTA_SPENT", None, 7),
        ("unavailable", "UNAVAILABLE", "unavailable", "UNAVAILABLE", None, 1),
        ("internal", "INTERNAL", "internal", "INTERNAL", None, None),
    ],
)
def test_each_kind_answers_its_code_and_its_details(
    call: Any,
    how: str,
    code: str,
    kind: str,
    reason: str,
    extra: str | None,
    retry: int | None,
) -> None:
    error, _, details = _refused(call, {"how": how})
    info = details["ErrorInfo"]

    assert error.code() is getattr(grpc.StatusCode, code)
    assert (info.reason, info.domain, info.metadata["kind"]) == (reason, "billing.v1", kind)
    if extra:
        assert extra in details
    if retry is None:
        assert "RetryInfo" not in details
    else:
        assert details["RetryInfo"].retry_delay.seconds == retry


@pytest.mark.parametrize("how", ["internal", "unavailable"])
def test_the_inside_of_the_process_is_never_told(call: Any, how: str) -> None:
    error, status, details = _refused(call, {"how": how})
    everything = f"{error.details()}{status}{details}"

    assert "hunter2" not in everything
    assert "postgres" not in everything
    assert "RuntimeError" not in everything and "LedgerDown" not in everything


def test_an_auth_refusal_keeps_its_trailer_beside_the_status(call: Any) -> None:
    error, _, details = _refused(call, {"how": "permission_denied"})
    trailing = {str(key): value for key, value in error.trailing_metadata()}

    assert json.loads(trailing["sp-auth-refusal"])["requirement"] == "invoices:issue"
    assert details["ErrorInfo"].metadata["requirement"] == "invoices:issue"


def test_a_domain_refusal_names_the_rule_it_broke(call: Any) -> None:
    _, _, details = _refused(call, {"how": "domain"})
    violation = details["PreconditionFailure"].violations[0]

    assert violation.type == "CONTRACT_VIOLATION"
    assert violation.description == "an invoice has to balance"


def test_a_validation_error_is_a_bad_request_per_field(call: Any) -> None:
    error, _, details = _refused(call, {"how": "domain", "amount": "a lot"})
    violations = {
        one.field: one.description for one in details["BadRequest"].field_violations
    }

    assert error.code() is grpc.StatusCode.INVALID_ARGUMENT
    assert details["ErrorInfo"].reason == "INVALID"
    assert set(violations) == {"amount"}
    assert violations["amount"]
