"""Readiness apart from liveness: checks a project registers by name — a binary, a key, a store —
each bounded in time, run at once, and a failing one answered 503 with the problem naming it, the
same on REST, FastAPI and JSON-RPC."""

import threading
import time
from datetime import timedelta

import pytest
from starlette.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.entrypoints import ReadinessChecks, RestGateway, RpcGateway

QUICK = timedelta(milliseconds=200)


class Ask(DataTransferObject):
    n: int


class Answer(DataTransferObject):
    total: int


def _bus() -> UseFramework:
    framework = UseFramework("readiness-billing", log_after_execution=False)

    @framework.feature(Ask)
    class DoIt(Feature):
        def execute(self, dto: Ask) -> Answer:
            return Answer(total=dto.n)

    framework.build_root_bus()
    return framework


def _down() -> None:
    raise ConnectionError("valkey:6379 refused the connection")


def test_every_check_that_passes_makes_the_service_ready():
    checks = ReadinessChecks().add("binary", lambda: True).add("key", lambda: "set")

    report = checks.report()

    assert report.ready
    assert [(one.name, one.ready, one.detail) for one in report.checks] == [
        ("binary", True, None),
        ("key", True, None),
    ]


def test_a_check_that_raises_or_answers_false_says_why_it_is_not_ready():
    checks = ReadinessChecks().add("valkey", _down).add("tesseract", lambda: False)

    report = checks.report()

    assert not report.ready
    assert [(one.name, one.detail) for one in report.failing()] == [
        ("valkey", "ConnectionError: valkey:6379 refused the connection"),
        ("tesseract", "the check answered False"),
    ]


def test_a_check_that_does_not_answer_in_its_time_is_not_ready_and_checks_run_at_once():
    released = threading.Event()
    checks = (
        ReadinessChecks(timeout=QUICK)
        .add("store", lambda: released.wait(5))
        .add("service", lambda: released.wait(5))
    )

    started = time.monotonic()
    report = checks.report()
    took = time.monotonic() - started
    released.set()

    assert not report.ready
    assert {one.detail for one in report.checks} == {"did not answer within 0.2s"}
    assert took < 2 * QUICK.total_seconds()  # at once: the slowest timeout, never their sum


def test_a_check_still_running_is_waited_on_again_never_started_twice():
    released = threading.Event()
    started: list[int] = []

    def hangs() -> bool:
        started.append(1)
        return released.wait(5)

    checks = ReadinessChecks(timeout=QUICK).add("store", hangs)

    first, second = checks.report(), checks.report()
    assert (first.ready, second.ready, len(started)) == (False, False, 1)

    released.set()
    third = checks.report()  # the run that hung, now answered
    fourth = checks.report()  # a run of its own

    assert (third.ready, fourth.ready, len(started)) == (True, True, 2)


def test_each_check_has_its_own_timeout():
    checks = ReadinessChecks(timeout=timedelta(seconds=5)).add(
        "slow", lambda: time.sleep(1), timeout=timedelta(milliseconds=50)
    )

    assert checks.report().checks[0].detail == "did not answer within 0.05s"


def test_a_gateway_s_first_check_is_its_buses_and_a_project_adds_its_own():
    gateway = RestGateway({"billing": _bus()})
    gateway.readiness.add("valkey", _down)

    report = gateway.readiness.report()

    assert [one.name for one in report.checks] == ["buses", "valkey"]
    assert report.checks[0].ready and not report.ready


def test_rest_answers_readiness_apart_from_liveness():
    gateway = RestGateway({"billing": _bus()})
    client = TestClient(gateway.app())

    ready = client.get("/readyz")
    gateway.readiness.add("valkey", _down)
    not_ready = client.get("/readyz")

    assert (ready.status_code, ready.json()["ready"]) == (200, True)
    assert not_ready.status_code == 503
    assert client.get("/healthz").status_code == 200  # alive: nothing to restart
    said = not_ready.json()
    assert (said["kind"], said["next_step"]) == ("unavailable", "retry_later")
    assert said["detail"] == (
        "not ready: valkey (ConnectionError: valkey:6379 refused the connection)"
    )
    assert [(one["name"], one["ready"]) for one in said["checks"]] == [
        ("buses", True),
        ("valkey", False),
    ]


def test_json_rpc_answers_readiness_and_shares_the_checks_of_another_gateway():
    bus = _bus()
    rest, rpc = RestGateway({"billing": bus}), RpcGateway({"billing": bus})
    rest.readiness.add("tesseract", lambda: False)
    rpc.readiness = rest.readiness

    answered = TestClient(rpc.app()).get("/readyz")

    assert answered.status_code == 503
    assert answered.json()["detail"] == "not ready: tesseract (the check answered False)"


def test_fastapi_answers_readiness_as_a_problem():
    pytest.importorskip("fastapi")
    from sincpro_framework.entrypoints import FastApiGateway

    gateway = FastApiGateway([_bus()], unguarded=True)
    gateway.readiness.add("valkey", _down)

    answered = TestClient(gateway.app()).get("/readyz")

    assert answered.status_code == 503
    assert answered.headers["content-type"] == "application/problem+json"
    assert answered.json()["checks"][1]["name"] == "valkey"


def test_a_probe_path_set_to_none_is_not_served():
    client = TestClient(RestGateway({"billing": _bus()}).app(routes=()))
    routes = RestGateway({"billing": _bus()}).routes(ready_path=None)

    assert client.get("/readyz").status_code == 200
    assert "/readyz" not in {route.path for route in routes}
