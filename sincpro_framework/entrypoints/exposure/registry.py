"""The registry: every binding, by the handler class it was declared on — never written onto the
class — and the latest class declared under each name, which is how a replaced handler named by
`module.Class` is found.

Context: the same shape as `AccessControl`'s declarations. Weak on both sides, so a use case
loaded again lets its old class go. A `replaces=` handler inherits, wire by wire, the binding of
the one it replaces when it declares none for that wire — replacing a handler never moves or
drops a public operation; the composition's `exclude` does that on purpose.
"""

from collections.abc import Iterable, Mapping
from weakref import WeakKeyDictionary, WeakValueDictionary

from sincpro_framework.entrypoints.exposure.bindings import Binding
from sincpro_framework.exceptions import ExtensionRefused


class ExposureRefused(ExtensionRefused):
    """An exposure that cannot hold as declared — a second binding for one wire on a class, or
    a surface the build refuses (PRD_14 §7), every reason listed at once."""


def qualified(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


class Bindings:
    def __init__(self) -> None:
        self._by_class: WeakKeyDictionary[type, dict[str, Binding]] = WeakKeyDictionary()
        self._by_name: WeakValueDictionary[str, type] = WeakValueDictionary()

    def declare[T: type](self, cls: T, binding: Binding) -> T:
        """Context: one binding per wire — a second one for the same wire is refused, since
        only one of them could be published."""
        held = self._by_class.setdefault(cls, {})
        if binding.wire in held:
            raise ExposureRefused(
                f"{cls.__name__} declares two {binding.wire} bindings — {held[binding.wire]!r} "
                f"and {binding!r}: declare one"
            )
        held[binding.wire] = binding
        self._by_name[qualified(cls)] = cls
        return cls

    def of(self, cls: type, replaced: Iterable[str] = ()) -> Mapping[str, Binding]:
        """Every binding `cls` answers with, by wire — its own, and for a wire it declares
        nothing on, the newest of the handlers it replaced that did (`replaced` oldest first).
        """
        found: dict[str, Binding] = {}
        for name in reversed(tuple(replaced)):
            older = self._by_name.get(name)
            if older is None:
                continue
            for wire, binding in self._by_class.get(older, {}).items():
                found.setdefault(wire, binding)
        return {**found, **self._by_class.get(cls, {})}

    def declared(self) -> list[type]:
        return list(self._by_name.values())


registry = Bindings()
"""The process's one registry — every decorator writes here, every gateway reads it."""


def declare[T: type](cls: T, binding: Binding) -> T:
    """Record `binding` for the handler `cls` — what every exposure decorator does, and what a
    project's own transport's decorator does with its own binding type."""
    return registry.declare(cls, binding)


def bindings_of(cls: type, replaced: Iterable[str] = ()) -> Mapping[str, Binding]:
    return registry.of(cls, replaced)
