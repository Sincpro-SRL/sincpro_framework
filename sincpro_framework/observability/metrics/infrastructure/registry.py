"""Where declarations live: by class, never on the class (as the exposure bindings)."""

from weakref import WeakKeyDictionary

from sincpro_framework.observability.metrics.domain.declarations import Declaration

_by_class: "WeakKeyDictionary[type, tuple[Declaration, ...]]" = WeakKeyDictionary()


def declare(cls: type, declaration: Declaration) -> None:
    _by_class[cls] = (*_by_class.get(cls, ()), declaration)


def declarations_of(cls: type) -> tuple[Declaration, ...]:
    """What `cls` declares, and what the classes it inherits from declared."""
    found: list[Declaration] = []
    for one in reversed(cls.__mro__):
        found.extend(_by_class.get(one, ()))
    return tuple(found)
