import pytest
from structlog.testing import capture_logs

from sincpro_framework import DataTransferObject, Feature, UseFramework, exceptions


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

    with pytest.raises(exceptions.DependencyNotRegistered, match="missing_adapter"):
        _ = framework.deps.missing_adapter

    with pytest.raises(exceptions.DependencyNotRegistered, match="Available: adapter_1"):
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


class Counted(DataTransferObject):
    by: str


class OtherAdapter:
    def ping(self) -> str:
        return "other"


def _pinging(name: str) -> UseFramework:
    framework = UseFramework(name, log_after_execution=False)

    @framework.feature(CommandNoop)
    class Ping(Feature):
        adapter: FakeAdapter

        def execute(self, dto: CommandNoop) -> Counted:
            return Counted(by=self.adapter.ping())

    return framework


def test_a_second_dependency_under_a_taken_name_is_refused_naming_what_is_there():
    framework = UseFramework("deps-taken", log_after_execution=False)
    framework.add_dependency("adapter", FakeAdapter())

    with pytest.raises(
        exceptions.DependencyAlreadyRegistered, match="adapter.*FakeAdapter.*replaces="
    ):
        framework.add_dependency("adapter", OtherAdapter())


def test_a_dependency_replaced_after_the_build_answers_from_the_next_execution():
    framework = _pinging("deps-replaced")
    real = FakeAdapter()
    framework.add_dependency("adapter", real)
    assert framework(CommandNoop(), Counted).by == "pong"

    framework.add_dependency("adapter", OtherAdapter(), replaces=real)

    assert framework(CommandNoop(), Counted).by == "other"
    assert isinstance(framework.deps.adapter, OtherAdapter)


def test_replacing_what_is_not_registered_works_with_a_warning():
    framework = _pinging("deps-replaced-mismatch")
    framework.add_dependency("adapter", FakeAdapter())

    with capture_logs() as logs:
        framework.add_dependency("adapter", OtherAdapter(), replaces=OtherAdapter())

    assert framework(CommandNoop(), Counted).by == "other"
    warned = [line["event"] for line in logs if line["log_level"] == "warning"]
    assert any(
        "replaces OtherAdapter, but FakeAdapter is registered" in one for one in warned
    )


def test_a_fresh_bus_repeats_the_replacement():
    framework = _pinging("deps-replaced-fresh")
    real = FakeAdapter()
    framework.add_dependency("adapter", real)
    framework.add_dependency("adapter", OtherAdapter(), replaces=real)

    assert framework.fresh()(CommandNoop(), Counted).by == "other"
