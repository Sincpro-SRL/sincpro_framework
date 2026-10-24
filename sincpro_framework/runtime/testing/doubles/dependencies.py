"""Replace a registered dependency with a double for the length of a test — and find the ones a
handler declares that nobody registered.

Context: a handler receives its dependencies when its generation of the bus is built. A double
takes the place of the real one with `add_dependency(..., replaces=)`, so the next execution
runs on a generation built with it, and the real one comes back the same way.
"""

import inspect
from contextlib import contextmanager
from typing import Any, Generator

from sincpro_framework.runtime.testing import exceptions
from sincpro_framework.use_bus import UseFramework


def _built_handlers(framework: UseFramework) -> list[Any]:
    """Every Feature and ApplicationService instance of ``framework``'s bus, built now if it
    was not — refused for a bus hosted by another service, whose handlers are not here."""
    if framework.is_reference:
        raise exceptions.SincproFrameworkNotBuilt(
            f"'{framework.name}' is hosted by another service: nothing of it is built here, so "
            "it has no dependency to replace — replace it where it runs"
        )
    framework.build_root_bus()
    bus = framework.bus
    if bus is None:
        raise exceptions.SincproFrameworkNotBuilt(
            f"'{framework.name}' was built, but it has no bus"
        )
    features = bus.feature_bus.feature_registry.values()
    app_services = bus.app_service_bus.app_service_registry.values()
    return [*features, *app_services]


@contextmanager
def override_dependencies(
    framework: UseFramework, **doubles: Any
) -> Generator[UseFramework, None, None]:
    """Every Feature and ApplicationService of ``framework`` sees ``doubles`` inside the block.

    Context: meant for tests. Each double takes the place of the real one for the executions
    that start inside the block; one already running keeps what it had.

    1. Refuse a name that was never registered — a typo would otherwise leave the real
       adapter in place and the test would silently talk to it.
    2. Refuse a bus hosted by another service: nothing of it runs here.
    3. Each double in the place of what is registered (``framework.deps`` answers it).
    Final: what was there before, back in its place, even when the block raises. Overrides
    nest.

    Example::

        with override_dependencies(sales, partner_lookup=FakePartnerLookup()):
            result = sales(CommandCreateContact(name="Ana"), ResponseCreateContact)
    """
    unknown = sorted(set(doubles) - set(framework.dynamic_dep_registry))
    if unknown:
        available = ", ".join(sorted(framework.dynamic_dep_registry)) or "(none)"
        raise exceptions.DependencyNotRegistered(
            f"Cannot override {', '.join(unknown)}: not registered with add_dependency. "
            f"Available: {available}"
        )
    if framework.is_reference:
        raise exceptions.SincproFrameworkNotBuilt(
            f"'{framework.name}' is hosted by another service: nothing of it is built here, so "
            "it has no dependency to replace — replace it where it runs"
        )

    previous = {name: framework.dynamic_dep_registry[name] for name in doubles}
    for name, double in doubles.items():
        framework.add_dependency(name, double, replaces=previous[name])
    try:
        yield framework
    finally:
        for name, real in previous.items():
            framework.add_dependency(name, real, replaces=doubles[name])


def _declared(handler: type) -> set[str]:
    """The names a handler class and the project's classes it inherits annotate — never the
    framework's own, which the bus gives every handler itself."""
    names: set[str] = set()
    for klass in handler.__mro__:
        if klass is object or klass.__module__.split(".")[0] == "sincpro_framework":
            continue
        names.update(inspect.get_annotations(klass))
    return names


def unregistered_dependencies(framework: UseFramework) -> dict[str, tuple[str, ...]]:
    """Each handler that declares a dependency nobody registered on ``framework`` → those
    names, sorted — empty when the wiring is whole.

        assert unregistered_dependencies(billing) == {}

    Context: a handler reads its dependencies as attributes, typed on a `DependencyContextType`
    for the IDE, and one that was declared but never registered fails only when the Feature
    runs. Spring and NestJS refuse to start instead; here it is one assertion in the project's
    suite, so a service that registers some dependencies late keeps starting as it does.
    """
    registered = set(framework.dynamic_dep_registry)
    missing: dict[str, tuple[str, ...]] = {}
    for handler in _built_handlers(framework):
        declared = type(handler)
        absent = sorted(_declared(declared) - registered)
        if absent:
            missing[f"{declared.__module__}.{declared.__qualname__}"] = tuple(absent)
    return missing
