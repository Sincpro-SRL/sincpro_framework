"""REST on FastAPI — the one call path and what it answers (PRD_15 §1.2, §1.3, §2.3, §2.4, §6).

The incidents: an identity set in one worker thread and lost before the bus (the contextvar
trap), so a guarded use case answers 401 to a caller who authenticated; a DTO validated twice —
a Value Object's check run twice, a nested model rebuilt; a hand-written route that answers
differently from a generated one; a failure answered with the inside of the process, or a
retryable one without `Retry-After`; two concurrent retries with one `Idempotency-Key` both
running the write.
"""

import threading
import time
from datetime import timedelta
from typing import Any, ClassVar

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import model_validator

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    Identity,
    Permission,
    StaticProvider,
    current_identity,
)
from sincpro_framework.common.failures import FailureKind
from sincpro_framework.common.store import InMemoryKeyValue
from sincpro_framework.data_layer.caching import AlreadyInProgress, Idempotency, KeyReused
from sincpro_framework.ddd.exceptions import DomainError, StaleAggregate
from sincpro_framework.ddd.query import Query
from sincpro_framework.entrypoints.adapters.fastapi import (
    PROBLEM_JSON,
    BusCall,
    FastApiGateway,
    bus_call,
    install_problem_handlers,
)
from sincpro_framework.entrypoints.entrypoint.decorators import rest


class Perm(Permission):
    ISSUE = "billing.invoice.issue"


class Counted(DataTransferObject):
    """Counts every validation of the DTO — a spy on 'validated once'."""

    validations: ClassVar[list[str]] = []

    @model_validator(mode="before")
    @classmethod
    def _count(cls, data: Any) -> Any:
        cls.validations.append(cls.__name__)
        return data


class CommandIssueInvoice(Counted):
    total: int


class Issued(DataTransferObject):
    number: str
    by: str


class CommandCancelInvoice(Counted):
    invoice_id: str
    why: str


class QueryInvoice(Counted, Query):
    invoice_id: str


class Invoice(DataTransferObject):
    invoice_id: str


class CommandFail(DataTransferObject):
    how: str


class InvoiceNotFound(DomainError):
    failure_kind = FailureKind.NOT_FOUND


class QuotaSpent(DomainError):
    failure_kind = FailureKind.EXHAUSTED
    retry_after = 30


class LedgerDown(Exception):
    failure_kind = FailureKind.UNAVAILABLE


FAILURES: dict[str, Exception] = {
    "not_found": InvoiceNotFound("invoice F-9 does not exist"),
    "conflict": StaleAggregate("invoice F-1 was written by someone else"),
    "in_progress": AlreadyInProgress("still running"),
    "key_reused": KeyReused("another payload"),
    "domain": DomainError("an invoice has to balance"),
    "exhausted": QuotaSpent("the monthly quota is spent"),
    "unavailable": LedgerDown("ledger at 10.0.0.3 refused"),
    "internal": RuntimeError("postgres://admin:hunter2@db — never to the caller"),
}


def _guarded() -> tuple[UseFramework, AccessControl[Perm]]:
    auth = AccessControl[Perm](
        providers=[
            StaticProvider(
                {
                    "t-1": Identity.user("user:1", permissions={Perm.ISSUE}),
                    "t-2": Identity.user("user:2"),
                }
            )
        ]
    )
    bus = UseFramework("billing", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @auth.requires(Perm.ISSUE)
    @rest.post("/invoices", status=201)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Issued:
            return Issued(number=f"F-{dto.total}", by=current_identity().subject)

    @bus.feature(CommandCancelInvoice)
    @auth.requires(Perm.ISSUE)
    @rest.post("/invoices/{invoice_id}/cancel")
    class CancelInvoice(Feature):
        def execute(self, dto: CommandCancelInvoice) -> Invoice:
            return Invoice(invoice_id=dto.invoice_id)

    @bus.feature(QueryInvoice)
    @auth.public
    @rest.get("/invoices/{invoice_id}")
    class GetInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> Invoice:
            return Invoice(invoice_id=dto.invoice_id)

    @bus.feature(CommandFail)
    @auth.public
    @rest.post("/failures")
    class Fail(Feature):
        def execute(self, dto: CommandFail) -> None:
            raise FAILURES[dto.how]

    auth.on(bus)
    return bus, auth


@pytest.fixture
def bus() -> UseFramework:
    return _guarded()[0]


@pytest.fixture
def client(bus: UseFramework) -> TestClient:
    return TestClient(FastApiGateway([bus]).app(), raise_server_exceptions=False)


AUTHORIZED = {"authorization": "Bearer t-1"}


# Validate once


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("post", "/billing/invoices", {"total": 3}),
        ("post", "/billing/invoices/F-1/cancel", {"why": "duplicate"}),
        ("get", "/billing/invoices/F-1", None),
    ],
)
def test_the_dto_is_validated_exactly_once_per_request(
    client: TestClient, method: str, path: str, body: Any
) -> None:
    Counted.validations.clear()
    answered = client.request(method, path, json=body, headers=AUTHORIZED)
    assert answered.status_code in (200, 201), answered.text
    assert len(Counted.validations) == 1, Counted.validations


# One call path


def test_the_identity_authenticated_reaches_the_use_case(client: TestClient) -> None:
    """The contextvar regression: authentication and the bus run in one worker thread."""
    issued = client.post("/billing/invoices", json={"total": 1}, headers=AUTHORIZED)
    assert issued.status_code == 201
    assert issued.json() == {"number": "F-1", "by": "user:1"}


def _hand_written(bus: UseFramework) -> TestClient:
    app = FastAPI(separate_input_output_schemas=False)
    install_problem_handlers(app)
    router = APIRouter(prefix="/mine")

    @router.post("/invoices", status_code=201)
    async def issue(
        cmd: CommandIssueInvoice, call: BusCall = Depends(bus_call(bus))
    ) -> Issued:
        return await call(cmd)

    @router.post("/failures")
    async def fail(cmd: CommandFail, call: BusCall = Depends(bus_call(bus))) -> None:
        return await call(cmd)

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "body, headers, route",
    [
        ({"total": 5}, AUTHORIZED, "invoices"),
        ({"total": 5}, {}, "invoices"),
        ({"total": 5}, {"authorization": "Bearer t-2"}, "invoices"),
        ({"total": "five"}, AUTHORIZED, "invoices"),
        ({"how": "domain"}, {}, "failures"),
        ({"how": "internal"}, {}, "failures"),
    ],
)
def test_a_hand_written_route_answers_exactly_what_the_generated_one_does(
    bus: UseFramework, client: TestClient, body: Any, headers: Any, route: str
) -> None:
    generated = client.post(f"/billing/{route}", json=body, headers=headers)
    mine = _hand_written(bus).post(f"/mine/{route}", json=body, headers=headers)
    assert generated.status_code == mine.status_code
    expected = generated.json()
    got = mine.json()
    if isinstance(expected, dict) and "instance" in expected:
        assert got.pop("instance") == f"/mine/{route}"
        expected.pop("instance")
    assert got == expected
    assert generated.headers.get("www-authenticate") == mine.headers.get("www-authenticate")


# Failures, as problems


@pytest.mark.parametrize(
    "how, status, reason, retry",
    [
        ("not_found", 404, "INVOICE_NOT_FOUND", None),
        ("conflict", 409, "STALE_AGGREGATE", None),
        ("in_progress", 409, "ALREADY_IN_PROGRESS", "1"),
        ("key_reused", 422, "KEY_REUSED", None),
        ("domain", 422, "DOMAIN_ERROR", None),
        ("exhausted", 429, "QUOTA_SPENT", "30"),
        ("unavailable", 503, "UNAVAILABLE", "1"),
        ("internal", 500, "INTERNAL", None),
    ],
)
def test_each_failure_kind_answers_its_status_as_a_problem(
    client: TestClient, how: str, status: int, reason: str, retry: str | None
) -> None:
    answered = client.post("/billing/failures", json={"how": how})
    assert answered.status_code == status
    assert answered.headers["content-type"] == PROBLEM_JSON
    assert answered.headers.get("retry-after") == retry
    problem = answered.json()
    assert problem["kind"] == how
    assert problem["reason"] == reason
    assert problem["status"] == status
    assert problem["type"] == f"urn:sincpro:problem:{how}"
    assert problem["instance"] == "/billing/failures"
    if how == "internal" or how == "unavailable":
        assert "detail" not in problem
        assert "hunter2" not in answered.text and "10.0.0.3" not in answered.text
    else:
        assert problem["detail"] == str(FAILURES[how])


def test_unauthenticated_is_a_401_with_its_challenge(client: TestClient) -> None:
    refused = client.post("/billing/invoices", json={"total": 1})
    assert refused.status_code == 401
    assert refused.headers["www-authenticate"] == "Bearer"
    assert refused.json()["kind"] == "unauthenticated"


def test_permission_denied_is_a_403_saying_what_is_required(client: TestClient) -> None:
    refused = client.post(
        "/billing/invoices", json={"total": 1}, headers={"authorization": "Bearer t-2"}
    )
    assert refused.status_code == 403
    assert refused.json()["requirement"] == "billing.invoice.issue"


def test_what_does_not_validate_is_a_422_pointing_at_each_field(client: TestClient) -> None:
    refused = client.post("/billing/invoices", json={"total": "x"}, headers=AUTHORIZED)
    assert refused.status_code == 422
    problem = refused.json()
    assert (problem["kind"], problem["reason"]) == ("invalid", "INVALID")
    assert [one["pointer"] for one in problem["errors"]] == ["#/total"]


def test_what_cannot_be_read_is_a_400(client: TestClient) -> None:
    refused = client.post(
        "/billing/invoices",
        content=b"{not json",
        headers={**AUTHORIZED, "content-type": "application/json"},
    )
    assert refused.status_code == 400
    assert refused.headers["content-type"] == PROBLEM_JSON


def test_starlettes_404_and_405_answer_problems(client: TestClient) -> None:
    missing = client.get("/nowhere")
    assert (missing.status_code, missing.json()["kind"]) == (404, "not_found")
    wrong = client.get("/billing/invoices")
    assert wrong.status_code == 405
    assert wrong.headers["content-type"] == PROBLEM_JSON
    assert "allow" in wrong.headers


def test_a_problem_carries_the_callers_trace(client: TestClient) -> None:
    trace = "4bf92f3577b34da6a3ce929d0e0e4736"
    refused = client.post(
        "/billing/failures",
        json={"how": "domain"},
        headers={"traceparent": f"00-{trace}-00f067aa0ba902b7-01"},
    )
    assert refused.json()["trace_id"] == trace


# Idempotency-Key


class CommandPay(DataTransferObject):
    amount: int


class CommandCharge(DataTransferObject):
    request_id: str
    amount: int

    def idempotency_key(self) -> str:
        return self.request_id


class Paid(DataTransferObject):
    receipt: int
    key: str | None


def _payments() -> tuple[UseFramework, list[int]]:
    idempotency = Idempotency(InMemoryKeyValue())
    runs: list[int] = []
    bus = UseFramework("payments", log_after_execution=False)

    @bus.feature(CommandPay)
    @rest.post("/payments")
    @idempotency.once(expires_after=timedelta(minutes=1))
    class Pay(Feature):
        def execute(self, dto: CommandPay) -> Paid:
            runs.append(dto.amount)
            return Paid(receipt=len(runs), key=self.context.get("idempotency_key"))

    @bus.feature(CommandCharge)
    @rest.post("/charges")
    @idempotency.once(expires_after=timedelta(minutes=1))
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> Paid:
            time.sleep(0.3)
            runs.append(dto.amount)
            return Paid(receipt=len(runs), key=None)

    return bus, runs


def test_the_idempotency_key_reaches_the_bus_and_is_required_when_nothing_else_keys() -> None:
    bus, runs = _payments()
    client = TestClient(FastApiGateway([bus], unguarded=True).app())
    missing = client.post("/payments/payments", json={"amount": 5})
    assert missing.status_code == 400
    assert missing.json()["reason"] == "IDEMPOTENCY_KEY_MISSING"
    assert runs == []
    first = client.post(
        "/payments/payments", json={"amount": 5}, headers={"Idempotency-Key": "k-1"}
    )
    again = client.post(
        "/payments/payments", json={"amount": 5}, headers={"Idempotency-Key": "k-1"}
    )
    other = client.post(
        "/payments/payments", json={"amount": 5}, headers={"Idempotency-Key": "k-2"}
    )
    assert first.json() == again.json() == {"receipt": 1, "key": "k-1"}
    assert other.json() == {"receipt": 2, "key": "k-2"}
    assert runs == [5, 5]


def test_a_command_that_keys_itself_needs_no_header_and_concurrent_retries_run_once() -> None:
    bus, runs = _payments()
    client = TestClient(FastApiGateway([bus], unguarded=True).app())
    answers: list[Any] = []

    def post() -> None:
        answers.append(
            client.post("/payments/charges", json={"request_id": "r-1", "amount": 9})
        )

    threads = [threading.Thread(target=post) for _ in range(3)]
    for one in threads:
        one.start()
    for one in threads:
        one.join()
    assert runs == [9]
    statuses = sorted(one.status_code for one in answers)
    assert statuses[0] == 200
    for refused in (one for one in answers if one.status_code != 200):
        assert refused.status_code == 409
        assert refused.headers["retry-after"] == "1"
        assert refused.json()["reason"] == "ALREADY_IN_PROGRESS"
