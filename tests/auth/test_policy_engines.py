"""Decisions taken outside the service: an AuthZEN PDP, OPA and OpenFGA asked over HTTP in the shape
each speaks, beside the provider whose identities they decide about — an engine that does not
answer is a 503, never a refusal it did not make."""

import json
from dataclasses import dataclass

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    AuthZenEngine,
    Credentials,
    DecidedBy,
    EndpointUnavailable,
    HttpRequest,
    Identity,
    OpaEngine,
    OpenFgaEngine,
    Permission,
    PermissionDenied,
    StaticProvider,
    security,
)
from sincpro_framework.ddd.criteria import Condition
from sincpro_framework.runtime.testing import ScriptedHttp

PDP = "https://pdp.internal"
ANA = Identity.user("user:ana", tenant="bo", roles={"accountant"})


@dataclass
class Invoice:
    id: str
    total: int = 0


class P(Permission):
    APPROVE = "billing.invoice.approve"


def body(request: HttpRequest) -> dict:
    return json.loads(request.body or b"{}")


def test_authzen_asks_subject_action_resource_and_context() -> None:
    http = ScriptedHttp(
        {
            f"POST {PDP}/access/v1/evaluation": lambda r: ScriptedHttp.answer(
                {"decision": body(r)["resource"]["id"] == "F-1"}
            )
        }
    )
    engine = AuthZenEngine(PDP, http=http, headers={"authorization": "Bearer pdp-token"})
    assert engine.decide(ANA, P.APPROVE, Invoice("F-1"), {"ip": "10.0.0.1"})
    assert not engine.decide(ANA, P.APPROVE, Invoice("F-2"))
    asked = body(http.sent[0])
    assert asked == {
        "subject": {
            "type": "user",
            "id": "user:ana",
            "properties": {"tenant": "bo", "roles": ["accountant"], "scopes": []},
        },
        "action": {"name": "billing.invoice.approve"},
        "resource": {"type": "invoice", "id": "F-1"},
        "context": {"ip": "10.0.0.1"},
    }
    assert http.sent[0].headers["authorization"] == "Bearer pdp-token"


def test_authzen_answers_a_batch_in_one_call() -> None:
    http = ScriptedHttp(
        {
            f"POST {PDP}/access/v1/evaluations": lambda r: ScriptedHttp.answer(
                {
                    "evaluations": [
                        {"decision": one["resource"]["id"] != "F-2"}
                        for one in body(r)["evaluations"]
                    ]
                }
            )
        }
    )
    assert AuthZenEngine(PDP, http=http).decide_many(
        ANA, P.APPROVE, [Invoice("F-1"), Invoice("F-2")]
    ) == [True, False]
    assert http.count(f"{PDP}/access/v1/evaluations") == 1


def test_opa_allows_on_a_true_result_only() -> None:
    http = ScriptedHttp({f"POST {PDP}/v1/data/billing/allow": {"result": True}})
    assert OpaEngine(PDP, "billing/allow", http=http).decide(ANA, P.APPROVE, Invoice("F-1"))
    assert body(http.sent[0])["input"]["action"] == "billing.invoice.approve"
    undefined = ScriptedHttp({f"POST {PDP}/v1/data/billing/allow": {"decision_id": "d-1"}})
    assert not OpaEngine(PDP, "billing/allow", http=undefined).decide(ANA, P.APPROVE)


def test_openfga_checks_relations_and_lists_what_may_be_read() -> None:
    store = f"{PDP}/stores/01H"
    http = ScriptedHttp(
        {
            f"POST {store}/check": lambda r: ScriptedHttp.answer(
                {"allowed": body(r)["tuple_key"]["relation"] == "approver"}
            ),
            f"POST {store}/batch-check": lambda r: ScriptedHttp.answer(
                {
                    "result": {
                        one["correlation_id"]: {"allowed": one["correlation_id"] == "0"}
                        for one in body(r)["checks"]
                    }
                }
            ),
            f"POST {store}/list-objects": {"objects": ["invoice:F-1", "invoice:F-7"]},
        }
    )
    engine = OpenFgaEngine(
        PDP,
        "01H",
        model_id="m-1",
        relations={P.APPROVE: "approver"},
        types={Invoice: "invoice"},
        http=http,
    )
    assert engine.decide(ANA, P.APPROVE, Invoice("F-1"))
    assert body(http.sent[0])["tuple_key"] == {
        "user": "user:ana",
        "relation": "approver",
        "object": "invoice:F-1",
    }
    assert engine.decide_many(ANA, P.APPROVE, [Invoice("F-1"), Invoice("F-2")]) == [
        True,
        False,
    ]
    scope = engine.scope(ANA, Invoice)
    assert scope is not None and isinstance(scope.selection, Condition)
    assert (scope.selection.field, scope.selection.value) == ("id", ["F-1", "F-7"])
    assert engine.scope(ANA, dict) is None


def test_an_engine_that_does_not_answer_is_unavailable_never_a_refusal() -> None:
    down = ScriptedHttp(
        {f"POST {PDP}/access/v1/evaluation": ScriptedHttp.answer({}, status=503)}
    )
    with pytest.raises(EndpointUnavailable):
        AuthZenEngine(PDP, http=down).decide(ANA, P.APPROVE)
    misconfigured = ScriptedHttp(
        {f"POST {PDP}/v1/data/x": ScriptedHttp.answer({"code": "unauthorized"}, status=401)}
    )
    with pytest.raises(EndpointUnavailable):
        OpaEngine(PDP, "x", http=misconfigured).decide(ANA, P.APPROVE)


class CommandApprove(DataTransferObject):
    invoice: str


class Approved(DataTransferObject):
    by: str


def test_an_engine_decides_beside_the_provider_whose_identities_it_judges() -> None:
    http = ScriptedHttp(
        {
            f"POST {PDP}/access/v1/evaluation": lambda r: ScriptedHttp.answer(
                {"decision": body(r)["subject"]["id"] == "user:ana"}
            )
        }
    )
    provider = DecidedBy(
        StaticProvider({"ana": ANA, "bea": Identity.user("user:bea")}, name="sessions"),
        AuthZenEngine(PDP, http=http),
    )
    auth = AccessControl[P](providers=[provider])
    billing = UseFramework("engine-billing", log_after_execution=False)

    @billing.feature(CommandApprove)
    @auth.requires(P.APPROVE)
    class Approve(Feature):
        def execute(self, dto: CommandApprove) -> Approved:
            return Approved(by=security.identity.subject)

    auth.on(billing)
    as_ana = Credentials(transport="http", headers={"authorization": "Bearer ana"})
    as_bea = Credentials(transport="http", headers={"authorization": "Bearer bea"})
    with security.authenticated_as(billing, as_ana):
        approved = billing(CommandApprove(invoice="F-1"), Approved)
    assert approved is not None and approved.by == "user:ana"
    with security.authenticated_as(billing, as_bea), pytest.raises(PermissionDenied):
        billing(CommandApprove(invoice="F-1"), Approved)
    assert provider.name == "sessions"
