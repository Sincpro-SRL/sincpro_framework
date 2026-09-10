"""One identity feeds both backends: Tempo's service.name and GlitchTip's release.

There are exactly two cases. A **library** — an installed distribution that builds a
bus inside somebody else's process — is identified by its own name and version, and
that wins: it is the code that produced the span. A **service** — an entrypoint that
is not a distribution — is identified by `APP_RELEASE`, the variable Sincpro sets on
every deployment.

`package=` exists as an escape hatch and is not used in practice.
"""

import pytest

from sincpro_framework.observability import ObservabilityIdentity, resolve_identity
from sincpro_framework.observability.domain import installed_version
from sincpro_framework.sincpro_conf import settings

A_LIBRARY = "sincpro_log"  # an installed distribution: sincpro-log
NOT_A_LIBRARY = "some_service_entrypoint"


@pytest.fixture(autouse=True)
def clean_identity_conf(monkeypatch):
    monkeypatch.setattr(settings, "app_release", None)
    monkeypatch.setattr(settings, "otel_service_name", None)


# ---------------------------------------------------------------------------
# Library beats service — the case that matters inside Odoo
# ---------------------------------------------------------------------------


def test_a_library_reports_its_own_version_not_the_deployments(monkeypatch):
    """An SDK inside Odoo must say which SDK version ran, not which Odoo.

    With APP_RELEASE describing the host, pairing it with the library's bus would
    make it impossible to tell which release of the SDK produced a failure.
    """
    monkeypatch.setattr(settings, "app_release", "sincpro-odoo:18.5.0-rc2")
    monkeypatch.setattr(settings, "otel_service_name", "odoo")

    identity = resolve_identity("siat-soap-sdk", module_name=A_LIBRARY)

    assert identity.artifact == "sincpro-log"
    assert identity.version == installed_version("sincpro-log")
    assert identity.release == f"sincpro-log:{installed_version('sincpro-log')}"


def test_a_service_entrypoint_falls_back_to_app_release(monkeypatch):
    """A service is not an installed distribution, so APP_RELEASE answers for it."""
    monkeypatch.setattr(settings, "app_release", "sincpro-odoo-mcp:0.8.0")

    identity = resolve_identity("common-mcp", module_name=NOT_A_LIBRARY)

    assert identity.service_name == "sincpro-odoo-mcp:0.8.0:common-mcp"


def test_an_artifact_is_never_paired_with_another_sources_version(monkeypatch):
    """Sources contribute whole pairs. Mixing produced identities like `odoo:1.2.3`.

    OTEL_SERVICE_NAME names the host; taking its name and then borrowing the
    library's version described a deployment that does not exist.
    """
    monkeypatch.setattr(settings, "otel_service_name", "odoo")

    identity = resolve_identity("siat-soap-sdk", module_name=A_LIBRARY)

    assert identity.artifact == "sincpro-log"
    assert identity.version != "unknown"
    assert not identity.service_name.startswith("odoo:")


# ---------------------------------------------------------------------------
# Values travel verbatim
# ---------------------------------------------------------------------------


def test_nothing_is_normalized(monkeypatch):
    """Rewriting `-` to `_` mangled both the name to search for and the version.

    `18.5.0-rc2` became `18.5.0_rc2`, and `sincpro-siat-soap` stopped matching the
    distribution it names. Grafana takes dashes fine — every other service uses them.
    """
    monkeypatch.setattr(settings, "app_release", "sincpro-odoo:18.5.0-rc2")

    identity = resolve_identity("siat-soap-sdk", module_name=NOT_A_LIBRARY)

    assert identity.service_name == "sincpro-odoo:18.5.0-rc2:siat-soap-sdk"
    assert identity.release == "sincpro-odoo:18.5.0-rc2"


# ---------------------------------------------------------------------------
# The remaining sources
# ---------------------------------------------------------------------------


def test_explicit_package_wins_over_everything(monkeypatch):
    """The escape hatch: what the caller declares beats what it can detect."""
    monkeypatch.setattr(settings, "app_release", "ignored:9.9.9")

    identity = resolve_identity(
        "common-mcp", package="sincpro-odoo-mcp", version="0.8.0", module_name=A_LIBRARY
    )

    assert identity.service_name == "sincpro-odoo-mcp:0.8.0:common-mcp"


def test_app_release_is_taken_verbatim_and_never_parsed(monkeypatch):
    """It always ships with its version, so there is nothing to split.

    Parsing it would also mangle a release that is not shaped `name:version` — a
    registry path, a bare build number.
    """
    monkeypatch.setattr(settings, "app_release", "registry.digitalocean.com/odoo:18.5.0-rc2")

    identity = resolve_identity("common-mcp", module_name=NOT_A_LIBRARY)

    assert identity.artifact == "registry.digitalocean.com/odoo:18.5.0-rc2"
    assert identity.version == ""
    assert identity.service_name == "registry.digitalocean.com/odoo:18.5.0-rc2:common-mcp"


def test_env_service_name_names_the_deployment_when_app_release_is_absent(monkeypatch):
    """No version to report, so no version segment — not a literal "unknown"."""
    monkeypatch.setattr(settings, "otel_service_name", "sincpro-odoo-mcp")

    identity = resolve_identity("helpdesk-mcp", module_name=NOT_A_LIBRARY)

    assert identity.service_name == "sincpro-odoo-mcp:helpdesk-mcp"


def test_the_bus_survives_even_when_nothing_resolves():
    """Unknown artifact is acceptable; an unattributable trace is not."""
    identity = resolve_identity("payment-cybersource", module_name=NOT_A_LIBRARY)

    assert identity.service_name == "unknown:payment-cybersource"


def test_two_buses_of_one_artifact_stay_distinguishable(monkeypatch):
    """Same deployment, two buses: only the last segment separates their traces."""
    monkeypatch.setattr(settings, "app_release", "sincpro-odoo-mcp:0.8.0")

    for bus in ("common-mcp", "sales-mcp"):
        assert resolve_identity(bus, module_name=NOT_A_LIBRARY).service_name == (
            f"sincpro-odoo-mcp:0.8.0:{bus}"
        )


def test_the_release_drops_the_bus_and_the_service_name_keeps_it(monkeypatch):
    """Two buses of one deployment ship one release; their traces stay separate."""
    monkeypatch.setattr(settings, "app_release", "sincpro-odoo-mcp:0.8.0")

    identity = resolve_identity("sales-mcp", module_name=NOT_A_LIBRARY)

    assert identity.release == "sincpro-odoo-mcp:0.8.0"
    assert identity.service_name == "sincpro-odoo-mcp:0.8.0:sales-mcp"


# ---------------------------------------------------------------------------
# Cost — the expensive lookup must stay out of the common paths
# ---------------------------------------------------------------------------


def test_the_expensive_scan_is_not_reached_when_a_library_resolves(monkeypatch):
    """The `_` → `-` convention answers for every Sincpro package in ~0.7ms.

    `packages_distributions()` costs ~200ms and does not cache; it exists only for
    a package whose import name differs from its distribution name.
    """
    monkeypatch.setattr(
        "sincpro_framework.observability.domain.packages_distributions",
        lambda: pytest.fail("the 200ms scan should not be reached"),
    )
    monkeypatch.setattr("sincpro_framework.observability.domain._distributions", None)

    assert resolve_identity("bus", module_name=A_LIBRARY).artifact == "sincpro-log"


def test_the_expensive_scan_is_not_reached_when_app_release_answers(monkeypatch):
    """A service must not pay the scan just because its entrypoint is not a package."""
    monkeypatch.setattr(settings, "app_release", "sincpro-odoo-mcp:0.8.0")
    monkeypatch.setattr(
        "sincpro_framework.observability.domain.packages_distributions",
        lambda: pytest.fail("the 200ms scan should not be reached"),
    )
    monkeypatch.setattr("sincpro_framework.observability.domain._distributions", None)

    assert resolve_identity("bus", module_name=NOT_A_LIBRARY).artifact == (
        "sincpro-odoo-mcp:0.8.0"
    )


def test_the_scan_runs_at_most_once_per_process(monkeypatch):
    """When it is reached, its result is cached for the life of the process."""
    from sincpro_framework.observability import domain

    calls: list[int] = []
    monkeypatch.setattr(domain, "_distributions", None)
    monkeypatch.setattr(
        domain,
        "packages_distributions",
        lambda: calls.append(1) or {"weird_import_name": ["some-dist"]},
    )

    domain._from_distribution_scan("weird_import_name")
    domain._from_distribution_scan("weird_import_name")

    assert len(calls) == 1


def test_identity_is_frozen():
    """Two backends read it; neither may rewrite what the other reports."""
    identity = ObservabilityIdentity(artifact="a", version="1", bus="b")

    with pytest.raises(Exception):
        identity.artifact = "other"  # type: ignore[misc]


def test_a_release_without_a_name_still_identifies_the_deployment(monkeypatch):
    """A bare build number is used as-is; the bus keeps the events separable."""
    monkeypatch.setattr(settings, "app_release", "2026.08.21")

    identity = resolve_identity("payments", module_name=NOT_A_LIBRARY)

    assert identity.service_name == "2026.08.21:payments"
    assert identity.release == "2026.08.21"
