"""Where declarations live: by class, never on the class (as the exposure bindings)."""

from weakref import WeakKeyDictionary

from sincpro_framework.observability.metrics.domain.declarations import MetricDeclaration

_by_class: "WeakKeyDictionary[type, tuple[MetricDeclaration, ...]]" = WeakKeyDictionary()


def declare(cls: type, declaration: MetricDeclaration) -> None:
    """Add `declaration` to what `cls` declares, after the ones before it — stored off the
    class, so a DTO or a use case is never touched and a class that goes away takes them
    with it."""
    _by_class[cls] = (*_by_class.get(cls, ()), declaration)


def declarations_of(cls: type) -> tuple[MetricDeclaration, ...]:
    """What `cls` declares, and what the classes it inherits from declared."""
    found: list[MetricDeclaration] = []
    for one in reversed(cls.__mro__):
        found.extend(_by_class.get(one, ()))
    return tuple(found)
