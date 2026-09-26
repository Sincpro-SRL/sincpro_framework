"""override_dependencies: the production wiring, with one adapter swapped for a test."""

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.exceptions import DependencyNotRegistered
from sincpro_framework.testing import override_dependencies


class Gateway:
    def charge(self) -> str:
        return "real"


class FakeGateway:
    def charge(self) -> str:
        return "fake"


class CommandCharge(DataTransferObject):
    pass


class CommandChargeTwice(DataTransferObject):
    pass


class ResponseCharge(DataTransferObject):
    result: str


def _framework() -> UseFramework:
    framework = UseFramework("override-deps", log_after_execution=False)
    framework.add_dependency("gateway", Gateway())

    @framework.feature(CommandCharge)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> ResponseCharge:
            return ResponseCharge(result=self.gateway.charge())

    @framework.app_service(CommandChargeTwice)
    class ChargeTwice(ApplicationService):
        def execute(self, dto: CommandChargeTwice) -> ResponseCharge:
            charged = self.feature_bus.execute(CommandCharge(), ResponseCharge)
            assert charged is not None
            return ResponseCharge(result=f"{charged.result}+{self.gateway.charge()}")

    return framework


def _charge(framework: UseFramework) -> str:
    response = framework(CommandCharge(), ResponseCharge)
    assert response is not None
    return response.result


def test_feature_sees_the_double_and_the_real_one_after():
    framework = _framework()

    with override_dependencies(framework, gateway=FakeGateway()):
        assert _charge(framework) == "fake"

    assert _charge(framework) == "real"


def test_application_service_and_its_features_see_the_double():
    framework = _framework()

    with override_dependencies(framework, gateway=FakeGateway()):
        response = framework(CommandChargeTwice(), ResponseCharge)

    assert response is not None
    assert response.result == "fake+fake"


def test_works_on_a_bus_that_already_executed():
    framework = _framework()
    assert _charge(framework) == "real"

    with override_dependencies(framework, gateway=FakeGateway()):
        assert _charge(framework) == "fake"


def test_deps_locator_answers_the_double():
    framework = _framework()
    fake = FakeGateway()

    with override_dependencies(framework, gateway=fake):
        assert framework.deps.gateway is fake

    assert isinstance(framework.deps.gateway, Gateway)


def test_restores_the_real_one_when_the_block_raises():
    framework = _framework()

    with pytest.raises(RuntimeError):
        with override_dependencies(framework, gateway=FakeGateway()):
            raise RuntimeError("test failed midway")

    assert _charge(framework) == "real"


def test_overrides_nest():
    framework = _framework()

    class OtherFake:
        def charge(self) -> str:
            return "other"

    with override_dependencies(framework, gateway=FakeGateway()):
        with override_dependencies(framework, gateway=OtherFake()):
            assert _charge(framework) == "other"
        assert _charge(framework) == "fake"

    assert _charge(framework) == "real"


def test_a_name_never_registered_is_refused():
    framework = _framework()

    with pytest.raises(DependencyNotRegistered, match="gatewy.*Available: gateway"):
        with override_dependencies(framework, gatewy=FakeGateway()):
            pass
