import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.exceptions import DependencyNotRegistered


class FakeAdapter:
    def ping(self) -> str:
        return "pong"


class CommandNoop(DataTransferObject):
    pass


def test_deps_returns_the_registered_instance():
    adapter = FakeAdapter()
    framework = UseFramework("deps-access", log_after_execution=False)
    framework.add_dependency("adapter_1", adapter)

    assert framework.deps.adapter_1 is adapter
    assert framework.deps.adapter_1.ping() == "pong"


def test_deps_getitem_matches_attribute_access():
    adapter = FakeAdapter()
    framework = UseFramework("deps-getitem", log_after_execution=False)
    framework.add_dependency("adapter_1", adapter)

    assert framework.deps["adapter_1"] is framework.deps.adapter_1


def test_deps_unknown_name_raises():
    framework = UseFramework("deps-missing", log_after_execution=False)
    framework.add_dependency("adapter_1", FakeAdapter())

    with pytest.raises(DependencyNotRegistered, match="missing_adapter"):
        _ = framework.deps.missing_adapter

    with pytest.raises(DependencyNotRegistered, match="Available: adapter_1"):
        _ = framework.deps["missing_adapter"]


def test_deps_getattr_default_still_works():
    framework = UseFramework("deps-getattr", log_after_execution=False)

    assert getattr(framework.deps, "missing_adapter", None) is None


def test_deps_is_read_only():
    framework = UseFramework("deps-readonly", log_after_execution=False)
    framework.add_dependency("adapter_1", FakeAdapter())

    with pytest.raises(AttributeError, match="read-only"):
        framework.deps.adapter_1 = FakeAdapter()

    with pytest.raises(AttributeError, match="read-only"):
        del framework.deps.adapter_1


def test_deps_contains_and_len():
    framework = UseFramework("deps-contains", log_after_execution=False)
    framework.add_dependency("adapter_1", FakeAdapter())

    assert "adapter_1" in framework.deps
    assert "adapter_2" not in framework.deps
    assert len(framework.deps) == 1


def test_deps_survives_bus_build():
    adapter = FakeAdapter()
    framework = UseFramework("deps-build", log_after_execution=False)
    framework.add_dependency("adapter_1", adapter)

    @framework.feature(CommandNoop)
    class NoopFeature(Feature):
        def execute(self, dto: CommandNoop) -> None:
            return None

    framework.build_root_bus()
    assert framework.deps.adapter_1 is adapter


def test_deps_are_isolated_per_instance():
    first = FakeAdapter()
    second = FakeAdapter()
    framework_a = UseFramework("deps-a", log_after_execution=False)
    framework_b = UseFramework("deps-b", log_after_execution=False)
    framework_a.add_dependency("adapter_1", first)
    framework_b.add_dependency("adapter_1", second)

    assert framework_a.deps.adapter_1 is first
    assert framework_b.deps.adapter_1 is second
