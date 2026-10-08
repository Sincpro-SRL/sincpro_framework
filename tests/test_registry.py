"""The process registry remembers the buses a service built, and refuses a second one by name."""

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.registry import (
    BusAlreadyRegistered,
    BusNotRegistered,
    Registry,
    registry,
)


def _bus(name: str) -> UseFramework:
    return UseFramework(name, log_after_execution=False)


def test_buses_come_back_in_the_order_they_were_added():
    held = Registry()
    billing, catalog = _bus("billing"), _bus("catalog")

    held.add(billing)
    held.add(catalog)

    assert held.all() == (billing, catalog)
    assert held.get("catalog") is catalog
    assert "billing" in held and len(held) == 2


def test_adding_the_same_bus_again_does_nothing():
    held = Registry()
    billing = _bus("billing")

    held.add(billing)
    held.add(billing)

    assert held.all() == (billing,)


def test_a_different_bus_under_the_same_name_is_refused():
    held = Registry()
    held.add(_bus("billing"))

    with pytest.raises(BusAlreadyRegistered) as refused:
        held.add(_bus("billing"))

    assert refused.value.name == "billing"
    assert len(held) == 1


def test_an_unknown_name_says_it_is_not_registered():
    held = Registry()

    with pytest.raises(BusNotRegistered) as missing:
        held.get("billing")

    assert missing.value.name == "billing"


def test_forget_drops_one_name_and_a_missing_name_is_nothing():
    held = Registry()
    billing = _bus("billing")
    held.add(billing)

    held.forget("billing")
    held.forget("billing")

    assert held.all() == ()
    held.add(billing)
    assert held.get("billing") is billing


def test_a_reference_is_not_held_and_one_this_process_serves_is():
    held = Registry()
    remote = _bus("registry-remote")
    remote.hosted_by("grpc://billing-service:50051?timeout=5")

    held.add(remote)

    assert remote.is_reference
    assert held.all() == () and "registry-remote" not in held
    with pytest.raises(BusNotRegistered):
        held.get("registry-remote")

    served = _bus("registry-served")
    served.hosted_by("grpc://billing-service:50051?timeout=5")
    served.run_here()
    held.add(served)

    assert not served.is_reference and held.all() == (served,)


def test_a_bus_pointed_elsewhere_after_it_was_added_leaves_the_registry():
    held = Registry()
    billing = _bus("registry-leaves")
    held.add(billing)

    billing.hosted_by("grpc://billing-service:50051?timeout=5")

    assert held.all() == ()
    with pytest.raises(BusNotRegistered):
        held.get("registry-leaves")


def test_a_bus_the_context_map_hosts_elsewhere_is_not_held(monkeypatch):
    monkeypatch.setenv("SINCPRO_CONTEXT_MAP", "registry-mapped=grpc://elsewhere:1?timeout=2")
    billing = _bus("registry-mapped")
    held = Registry()

    held.add(billing)

    assert billing.is_reference and held.all() == ()


def test_the_process_registry_is_the_one_entrypoints_import():
    billing = _bus("process-registry-billing")
    registry.add(billing)
    try:
        assert registry.get("process-registry-billing") is billing
    finally:
        registry.forget("process-registry-billing")
