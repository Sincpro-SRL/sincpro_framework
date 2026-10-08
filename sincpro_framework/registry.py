"""The buses one process added, in that order.

    from sincpro_framework.registry import registry

    registry.add(billing)
    registry.add(catalog)
    Subscriber(*registry.all())

A context adds itself when it is configured. An entrypoint imports those context packages —
that import is what builds them — and then reads the registry, instead of naming each bus
again. This module does not import contexts and does not sort them: the entrypoint's imports
are the build order. A cycle between contexts is already refused by the architecture check.

This is the memory of this process. Another replica builds its own. A context another
service hosts is reached through the context map, not through this registry. Creating a
`UseFramework` does not add it: tests build many buses under one name, and `fresh()` builds
a generation that is not the context's bus until the project adds it.
"""

import threading

from sincpro_framework.use_bus import UseFramework


class BusAlreadyRegistered(Exception):
    """A second, different bus was added under a name this process already holds."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"a bus named {name!r} is already registered in this process; "
            "a context registers once"
        )
        self.name = name


class BusNotRegistered(LookupError):
    """`get` was asked for a name nobody added."""

    def __init__(self, name: str) -> None:
        super().__init__(f"no bus named {name!r} is registered in this process")
        self.name = name


class Registry:
    """One process's buses, keyed by `UseFramework.name`, in the order `add` was called."""

    def __init__(self) -> None:
        self._buses: dict[str, UseFramework] = {}
        self._lock = threading.Lock()

    def _serving(self) -> None:
        """Drop a bus that now only forwards. The caller keeps that object; the context map
        says where the call goes."""
        for name, bus in list(self._buses.items()):
            if bus.is_reference:
                del self._buses[name]

    def add(self, bus: UseFramework) -> None:
        """Remember `bus`. Adding the same object again does nothing. A different bus under
        the same name is refused: two contexts in one process do not share a name.

        A reference (`hosted_by`, or the context map) is not remembered: this process does
        not serve it. `run_here` does, so that bus is remembered."""
        with self._lock:
            self._serving()
            if bus.is_reference:
                return
            current = self._buses.get(bus.name)
            if current is bus:
                return
            if current is not None:
                raise BusAlreadyRegistered(bus.name)
            self._buses[bus.name] = bus

    def get(self, name: str) -> UseFramework:
        """The bus registered under `name`."""
        with self._lock:
            self._serving()
            try:
                return self._buses[name]
            except KeyError:
                raise BusNotRegistered(name) from None

    def all(self) -> tuple[UseFramework, ...]:
        """Every bus this process serves, in the order they were added."""
        with self._lock:
            self._serving()
            return tuple(self._buses.values())

    def forget(self, name: str) -> None:
        """Drop one name. A missing name is nothing. A service does not forget a context it
        built; tests do, so one process can build the same name again."""
        with self._lock:
            self._serving()
            self._buses.pop(name, None)

    def __contains__(self, name: str) -> bool:
        with self._lock:
            self._serving()
            return name in self._buses

    def __len__(self) -> int:
        with self._lock:
            self._serving()
            return len(self._buses)


registry = Registry()

__all__ = ["BusAlreadyRegistered", "BusNotRegistered", "Registry", "registry"]
