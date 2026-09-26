"""Replace a registered dependency with a double for the length of a test.

Context: a Feature is built once per ``UseFramework`` and reused by every execution, and it
receives its dependencies when the bus is built. Registering a second value under the same
name raises, and changing ``dynamic_dep_registry`` after the build reaches nothing. So a test
that wants the production wiring with one adapter swapped had no supported way to ask for it.
"""

from contextlib import contextmanager
from typing import Any, Generator

from ..exceptions import DependencyNotRegistered
from ..use_bus import UseFramework


def _built_handlers(framework: UseFramework) -> list[Any]:
    framework.build_root_bus()
    assert framework.bus is not None
    features = framework.bus.feature_bus.feature_registry.values()
    app_services = framework.bus.app_service_bus.app_service_registry.values()
    return [*features, *app_services]


@contextmanager
def override_dependencies(
    framework: UseFramework, **doubles: Any
) -> Generator[UseFramework, None, None]:
    """Every Feature and ApplicationService of ``framework`` sees ``doubles`` inside the block.

    Context: meant for tests. It swaps the attribute on the instances the bus already holds,
    so it is visible to every thread using that bus; do not use it to change behaviour under
    live traffic.

    1. Refuse a name that was never registered — a typo would otherwise leave the real
       adapter in place and the test would silently talk to it.
    2. Build the bus, so the handlers that will run are the ones being patched.
    3. Swap the registry entry (``framework.deps``) and the attribute on every handler.
    Final: restore what was there before, even when the block raises. Overrides nest.

    Example::

        with override_dependencies(sales, partner_lookup=FakePartnerLookup()):
            result = sales(CommandCreateContact(name="Ana"), ResponseCreateContact)
    """
    unknown = sorted(set(doubles) - set(framework.dynamic_dep_registry))
    if unknown:
        available = ", ".join(sorted(framework.dynamic_dep_registry)) or "(none)"
        raise DependencyNotRegistered(
            f"Cannot override {', '.join(unknown)}: not registered with add_dependency. "
            f"Available: {available}"
        )

    handlers = _built_handlers(framework)
    previous = {name: framework.dynamic_dep_registry[name] for name in doubles}

    def _apply(values: dict[str, Any]) -> None:
        framework.dynamic_dep_registry.update(values)
        for handler in handlers:
            for name, value in values.items():
                setattr(handler, name, value)

    _apply(doubles)
    try:
        yield framework
    finally:
        _apply(previous)
