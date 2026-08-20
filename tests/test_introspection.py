"""introspection: rich metadata (FeatureOrAppServiceMetadata/DtoMetadata) over a built UseFramework."""

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.introspection import app_services, dtos, features


class Ping(DataTransferObject):
    """Ping a name."""

    name: str


class PingResponse(DataTransferObject):
    name: str


class Orchestrate(DataTransferObject):
    name: str


def _build_framework() -> tuple[UseFramework, type, type]:
    framework = UseFramework("introspection-test", log_after_execution=False)

    @framework.feature(Ping)
    class PingFeature(Feature):
        def execute(self, dto: Ping) -> PingResponse:
            """Say hi back."""
            return PingResponse(name=dto.name)

    @framework.app_service(Orchestrate)
    class OrchestrateService(ApplicationService):
        """Orchestrate a ping."""

        def execute(self, dto: Orchestrate) -> PingResponse:
            return self.feature_bus.execute(Ping(name=dto.name), PingResponse)

    framework.build_root_bus()
    return framework, PingFeature, OrchestrateService


def test_features_and_app_services_reflect_registrations():
    framework, _, _ = _build_framework()

    assert set(features(framework)) == {"Ping"}
    assert set(app_services(framework)) == {"Orchestrate"}
    assert {"Ping", "Orchestrate"} <= set(dtos(framework))


def test_feature_or_app_service_metadata_is_described_not_a_bare_instance():
    framework, PingFeature, OrchestrateService = _build_framework()

    ping = features(framework)["Ping"]
    assert ping.name == "Ping"
    assert ping.type is PingFeature
    assert isinstance(ping.instance, PingFeature)
    assert ping.dto is Ping
    assert ping.description == "Say hi back."  # no own class docstring, falls back to execute

    orchestrate = app_services(framework)["Orchestrate"]
    assert orchestrate.type is OrchestrateService
    assert orchestrate.dto is Orchestrate
    assert orchestrate.description == "Orchestrate a ping."  # own class docstring wins


def test_dto_metadata_carries_its_own_docstring_only():
    framework, _, _ = _build_framework()

    dto_metadata = dtos(framework)
    assert dto_metadata["Ping"].type is Ping
    assert dto_metadata["Ping"].description == "Ping a name."
    assert dto_metadata["Orchestrate"].description is None  # no own docstring


def test_raises_when_framework_not_built():
    framework = UseFramework("not-built", log_after_execution=False)

    with pytest.raises(ValueError):
        features(framework)
