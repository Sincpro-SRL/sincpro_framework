"""One identity feeds both backends: Tempo's service.name and GlitchTip's release.

``artifact:version:bus`` for traces, ``bus:version`` for errors — resolved once, from
the same sources, so the two can never disagree about which version is running.
"""

import pytest

from sincpro_framework.observability import resolve_identity
from sincpro_framework.sincpro_conf import settings


@pytest.fixture(autouse=True)
def clean_identity_conf(monkeypatch):
    monkeypatch.setattr(settings, "app_release", None)
    monkeypatch.setattr(settings, "otel_service_name", None)


def test_explicit_package_wins_over_every_env_source(monkeypatch):
    """What the bounded context declares beats what the deployment happens to set."""
    monkeypatch.setattr(settings, "app_release", "ignored:9.9.9")
    monkeypatch.setattr(settings, "otel_service_name", "from-env")

    identity = resolve_identity("common_mcp", package="sincpro-odoo-mcp", version="0.8.0")

    assert identity.service_name == "sincpro_odoo_mcp:0.8.0:common_mcp"


def test_app_release_names_the_artifact_and_the_version(monkeypatch):
    """APP_RELEASE is the Sincpro standard on services: `artifact:version`."""
    monkeypatch.setattr(settings, "app_release", "sincpro_mcp_odoo:0.8.0")
    monkeypatch.setattr(settings, "otel_service_name", "from-env")

    assert resolve_identity("sales_mcp").service_name == "sincpro_mcp_odoo:0.8.0:sales_mcp"


def test_bare_app_release_is_a_version_and_the_env_names_the_artifact(monkeypatch):
    """A service that ships only a version still gets a named artifact."""
    monkeypatch.setattr(settings, "app_release", "0.8.0")
    monkeypatch.setattr(settings, "otel_service_name", "sincpro_mcp_odoo")

    assert resolve_identity("common_mcp").service_name == "sincpro_mcp_odoo:0.8.0:common_mcp"


def test_env_service_name_alone_leaves_the_version_unknown(monkeypatch):
    monkeypatch.setattr(settings, "otel_service_name", "sincpro-odoo-mcp")

    assert (
        resolve_identity("helpdesk-mcp").service_name
        == "sincpro_odoo_mcp:unknown:helpdesk_mcp"
    )


def test_two_buses_of_one_artifact_stay_distinguishable(monkeypatch):
    """Same deployment, two buses: only the last segment separates their traces."""
    monkeypatch.setattr(settings, "app_release", "sincpro_mcp_odoo:0.8.0")

    assert resolve_identity("common_mcp").service_name == "sincpro_mcp_odoo:0.8.0:common_mcp"
    assert resolve_identity("sales_mcp").service_name == "sincpro_mcp_odoo:0.8.0:sales_mcp"


def test_the_bus_survives_even_when_nothing_else_resolves(monkeypatch):
    """Unknown artifact is acceptable; an unattributable trace is not."""
    monkeypatch.setattr(
        "sincpro_framework.observability.domain._from_caller_distribution", lambda: ("", "")
    )

    assert resolve_identity("payment-cybersource").service_name == (
        "unknown:unknown:payment_cybersource"
    )


def test_errors_and_traces_report_the_same_version(monkeypatch):
    """One object feeds both: the release is APP_RELEASE, the bus is only a tag."""
    monkeypatch.setattr(settings, "app_release", "sincpro_mcp_odoo:0.8.0")

    identity = resolve_identity("sales_mcp")

    assert identity.service_name == "sincpro_mcp_odoo:0.8.0:sales_mcp"
    assert identity.release == "sincpro_mcp_odoo:0.8.0"
    assert identity.artifact == "sincpro_mcp_odoo"


def test_the_distribution_lookup_runs_at_most_once_per_process(monkeypatch):
    """packages_distributions() costs ~200ms; it must not run per bus."""
    from sincpro_framework.observability import domain

    calls: list[int] = []
    monkeypatch.setattr(domain, "_distributions", None)
    monkeypatch.setattr(
        domain, "packages_distributions", lambda: calls.append(1) or {"tests": ["pytest"]}
    )

    domain._from_caller_distribution()
    domain._from_caller_distribution()

    assert len(calls) == 1
