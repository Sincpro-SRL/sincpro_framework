"""Contract of ``Observability`` — the only observability object the framework uses.

Whatever is installed or configured, every method here must be safe to call: the
bus runs the same with both backends down, and constructing one must not cost
anything, because every bus builds one whether it will trace or not.
"""

import pytest

from sincpro_framework.observability import Observability, registry
from sincpro_framework.sincpro_conf import settings


@pytest.fixture(autouse=True)
def clean_registry():
    registry.reset()
    yield
    registry.reset()


@pytest.fixture(autouse=True)
def no_backends_configured(monkeypatch):
    monkeypatch.setattr(settings, "otlp_endpoint", None)
    monkeypatch.setattr(settings, "sentry_dsn", None)
    monkeypatch.setattr(settings, "app_release", "sincpro_mcp_odoo:0.8.0")


def test_creating_one_resolves_no_identity_yet(monkeypatch):
    """Every bus builds one at import time; it must not pay for the lookup then."""
    monkeypatch.setattr(
        "sincpro_framework.observability.domain.resolve_identity",
        lambda *args, **kwargs: pytest.fail("identity resolved too early"),
    )

    Observability("common_mcp")


def test_app_release_answers_without_the_slow_distribution_lookup(monkeypatch):
    """The ~200ms scan must not run when APP_RELEASE already says who we are."""
    monkeypatch.setattr(
        "sincpro_framework.observability.domain.packages_distributions",
        lambda: pytest.fail("distribution scan should not be reached"),
    )
    monkeypatch.setattr("sincpro_framework.observability.domain._distributions", None)

    identity = Observability("common_mcp").identity

    assert identity.service_name == "sincpro_mcp_odoo:0.8.0:common_mcp"
    assert identity.release == "sincpro_mcp_odoo:0.8.0"


def test_status_before_start_says_not_built():
    assert Observability("common_mcp").status.model_dump() == {
        "sentry": {"active": False, "state": "off", "reason": "not_built"},
        "otel": {"active": False, "state": "off", "reason": "not_built"},
    }


def test_start_reports_each_backend_separately():
    """Neither backend configured is a reportable state, not a failure."""
    status = Observability("common_mcp").start()

    assert status.sentry.reason == "dsn_missing"
    assert status.otel.state == "off"


def test_a_span_is_still_a_usable_block_with_nothing_configured():
    observability = Observability("common_mcp")
    observability.start()

    entered = False
    with observability.span("PayDTO", "feature"):
        entered = True

    assert entered


def test_recording_an_error_without_backends_is_a_no_op():
    observability = Observability("common_mcp")
    observability.start()

    observability.record_error(RuntimeError("boom"), "PayDTO", "feature")


def test_ignored_error_types_accumulate_without_duplicates():
    class Expected(Exception):
        pass

    class AlsoExpected(Exception):
        pass

    observability = Observability("common_mcp")
    observability.ignore(Expected)
    observability.ignore(Expected, AlsoExpected)

    assert observability.ignored_errors == (Expected, AlsoExpected)
