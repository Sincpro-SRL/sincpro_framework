"""How the ``Observability`` object reaches the buses, and where its scope ends.

The container injects one instance per ``UseFramework`` into the three buses. Two
things can silently break that and neither shows up as a failing execution: a bus
holding a different instance (spans labelled with the wrong bus, ignored exception
types not applied), or two frameworks sharing one (a bounded context reporting
under its neighbour's identity). Both are pinned here.
"""

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework


class ChildDTO(DataTransferObject):
    pass


class ParentDTO(DataTransferObject):
    pass


def build_framework(name: str) -> UseFramework:
    framework = UseFramework(name, log_after_execution=False)

    @framework.feature(ChildDTO)
    class Child(Feature):
        def execute(self, dto: ChildDTO) -> str:
            return "ok"

    @framework.app_service(ParentDTO)
    class Parent(ApplicationService):
        def execute(self, dto: ParentDTO) -> str:
            self.feature_bus.execute(ChildDTO())
            return "ok"

    framework.build_root_bus()
    return framework


@pytest.fixture
def framework():
    return build_framework("payments")


def test_the_three_buses_share_the_framework_observability(framework):
    """One identity and one status for the whole framework, not three."""
    assert framework.bus is not None
    buses = (framework.bus, framework.bus.feature_bus, framework.bus.app_service_bus)

    assert all(bus.observability is framework.observability for bus in buses)


def test_the_feature_bus_injected_into_app_services_is_the_same_one(framework):
    """An ApplicationService delegating to a Feature must not cross into another bus.

    If the container handed out a second FeatureBus, that one would keep a default
    Observability and its spans would carry the wrong ``sincpro.instance``.
    """
    assert framework.bus is not None
    app_service = framework.bus.app_service_bus.app_service_registry["ParentDTO"]

    assert app_service.feature_bus is framework.bus.feature_bus


def test_two_frameworks_never_share_observability():
    """Each bounded context reports under its own identity, in the same process."""
    payments = build_framework("payments")
    billing = build_framework("billing")

    assert payments.observability is not billing.observability
    assert payments.observability.identity.bus == "payments"
    assert billing.observability.identity.bus == "billing"


def test_two_frameworks_never_share_a_bus():
    """The container's singletons are per framework, not per process."""
    payments = build_framework("payments")
    billing = build_framework("billing")

    assert payments.bus is not None and billing.bus is not None
    assert payments.bus.feature_bus is not billing.bus.feature_bus
    assert payments.bus.app_service_bus is not billing.bus.app_service_bus


def test_identity_is_resolved_once_and_shared(framework):
    """Resolving twice would mean paying the distribution lookup twice, and risking
    two different answers for one framework."""
    assert framework.bus is not None

    assert (
        framework.bus.feature_bus.observability.identity is framework.observability.identity
    )


def test_ignored_exceptions_reach_the_buses_without_being_propagated(framework):
    """Registering after the build must work: the buses hold the object, not a copy.

    This used to need an explicit `_propagate_sentry_config()` call after
    `build_root_bus()`; sharing one object is what removed it.
    """

    class Expected(Exception):
        pass

    framework.ignore_sentry_exceptions(Expected)

    assert framework.bus is not None
    assert Expected in framework.bus.feature_bus.observability.ignored_errors
    assert Expected in framework.bus.app_service_bus.observability.ignored_errors


def test_rebuilding_keeps_the_same_observability(framework):
    """A second build_root_bus() must not orphan the identity or the status."""
    before = framework.observability

    framework.build_root_bus()

    assert framework.observability is before
    assert framework.bus is not None
    assert framework.bus.feature_bus.observability is before


def test_a_bus_built_without_the_container_still_has_one():
    """FeatureBus is public: constructing it directly must not need observability."""
    from sincpro_framework.bus import FeatureBus
    from sincpro_framework.observability import Observability

    assert isinstance(FeatureBus().observability, Observability)
