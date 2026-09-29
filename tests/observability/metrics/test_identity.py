"""Who a metric comes from (PRD_03 §4.9): the service, stable across releases; the bounded
context, on every series; the library, version and tenant behind each context, on one info
series joined when asked.

The incidents: a release in the job name that restarts every series at each deploy and breaks
`rate()` across it; a library's bus inside a service whose version nobody can tell; a tenant's
dashboard that cannot tell its own numbers from another's.
"""

from collections.abc import Iterator

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.observability.domain import ObservabilityIdentity, installed_version
from sincpro_framework.observability.metrics import InMemoryRecorder, metrics
from sincpro_framework.observability.metrics.infrastructure.identity import (
    CONTEXT_INFO,
    metrics_resource,
)
from sincpro_framework.sincpro_conf import settings


@pytest.fixture
def recorder() -> Iterator[InMemoryRecorder]:
    recorded = InMemoryRecorder()
    with metrics.using(recorded):
        yield recorded


class CommandPing(DataTransferObject):
    pass


def _bus(name: str, package: str = "sincpro-framework") -> UseFramework:
    bus = UseFramework(name, package=package, log_after_execution=False)

    @bus.feature(CommandPing)
    class Ping(Feature):
        def execute(self, dto: CommandPing) -> None:
            return None

    return bus


def test_each_context_says_once_which_library_version_and_tenant_it_runs(
    recorder, monkeypatch
):
    monkeypatch.setattr(settings, "tenant", "acme")
    billing = _bus("billing")

    for _ in range(3):
        billing(CommandPing())

    assert recorder.totals(CONTEXT_INFO.name) == {
        (
            ("sincpro.artifact", "sincpro-framework"),
            ("sincpro.context", "billing"),
            ("sincpro.tenant", "acme"),
            ("sincpro.version", installed_version("sincpro-framework")),
        ): 1
    }


def test_two_contexts_of_one_process_are_two_info_series(recorder):
    _bus("billing")(CommandPing())
    _bus("siat-soap-sdk")(CommandPing())

    contexts = {
        dict(labels)["sincpro.context"] for labels in recorder.totals(CONTEXT_INFO.name)
    }
    assert contexts == {"billing", "siat-soap-sdk"}


def test_a_recorder_set_later_still_learns_who_the_context_is():
    """The info is announced to the recorder that records, whenever it was chosen."""
    bus = _bus("late")
    bus(CommandPing())

    later = InMemoryRecorder()
    with metrics.using(later):
        bus(CommandPing())

    assert len(later.totals(CONTEXT_INFO.name)) == 1


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        (
            ObservabilityIdentity(artifact="sincpro-odoo:18.5.0-rc2", version=""),
            ("sincpro-odoo", "18.5.0-rc2"),
        ),
        (
            ObservabilityIdentity(artifact="sincpro-siat-soap", version="8.0.3"),
            ("sincpro-siat-soap", "8.0.3"),
        ),
        (ObservabilityIdentity(artifact="odoo-bo", version=""), ("odoo-bo", "")),
        (
            # what `APP_RELEASE` is on a service: the image reference
            ObservabilityIdentity(
                artifact="registry.digitalocean.com/sincpro/sincpro_odoo_mcp:0.8.0"
            ),
            ("sincpro_odoo_mcp", "0.8.0"),
        ),
    ],
)
def test_the_release_is_split_into_a_stable_name_and_its_version(identity, expected):
    """`APP_RELEASE` travels verbatim to traces; a metric's service is its name alone."""
    assert (identity.service, identity.service_version) == expected


def test_the_metrics_resource_says_the_tenant_as_resource_tenant(monkeypatch):
    """`resource.tenant` is the canonical tenant of every Sincpro signal — Grafana and the
    Alloy pipeline read that key, and `deployment.environment.name` is an environment."""
    monkeypatch.setattr(settings, "tenant", "acme")

    attributes = metrics_resource(ObservabilityIdentity(artifact="sincpro-odoo", version="1"))

    assert attributes["tenant"] == "acme"
    assert "deployment.environment.name" not in attributes


def test_the_metrics_resource_keeps_the_version_out_of_the_service_name(monkeypatch):
    monkeypatch.setattr(settings, "tenant", "acme")

    attributes = metrics_resource(ObservabilityIdentity(artifact="sincpro-odoo:18.5.0-rc2"))

    assert attributes == {
        "service.name": "sincpro-odoo",
        "service.version": "18.5.0-rc2",
        "tenant": "acme",
    }


def test_without_a_tenant_nothing_is_invented(monkeypatch):
    monkeypatch.setattr(settings, "tenant", None)

    attributes = metrics_resource(
        ObservabilityIdentity(artifact="sincpro-odoo", version="1.0")
    )

    assert "tenant" not in attributes
