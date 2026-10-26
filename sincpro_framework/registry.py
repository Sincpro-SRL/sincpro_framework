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

from sincpro_framework import exceptions
from sincpro_framework.use_bus import UseFramework


class Registry:
    """One process's buses, keyed by `UseFramework.name`, in the order `add` was called."""

    def __init__(self) -> None:
        self._buses: dict[str, UseFramework] = {}
        self._lock = threading.Lock()

    def _serving(self) -> None:
        """Drop a bus that now only forwards. The caller still holds that object; the context map
        says where the call goes."""
        for name, bus in list(self._buses.items()):
            if bus.is_reference:
                del self._buses[name]

    def add(self, bus: UseFramework) -> None:
        """Remember `bus`. Adding the same object again does nothing. A different bus under
        the same name is refused: two contexts in one process do not share a name.

        A reference (`hosted_by`, or the context map) is not remembered: this process does
        not serve it. `run_here` does, so that bus is remembered.

            registry.add(billing); registry.add(billing)     ->  None, remembered once
            registry.add(<another bus named "billing">)      ->  BusAlreadyRegistered
            registry.add(<billing hosted_by elsewhere>)      ->  None, not remembered

        A bus that turned into a reference since it was added is dropped first.
        """
        with self._lock:
            self._serving()
            if bus.is_reference:
                return
            current = self._buses.get(bus.name)
            if current is bus:
                return
            if current is not None:
                raise exceptions.BusAlreadyRegistered(bus.name)
            self._buses[bus.name] = bus

    def get(self, name: str) -> UseFramework:
        """The bus registered under `name`.

            registry.get("billing")     ->  billing
            registry.get("shipping")    ->  BusNotRegistered("shipping")

        A bus that now only forwards is dropped first, so it is not found either.
        """
        with self._lock:
            self._serving()
            try:
                return self._buses[name]
            except KeyError:
                raise exceptions.BusNotRegistered(name) from None

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
