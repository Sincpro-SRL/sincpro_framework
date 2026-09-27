"""`BusRegistry`: the bus of a bounded context, as the code declares it plus the use cases stored
for it — rebuilt as a new generation when they change, and swapped in whole.

Context: a bus is not changed once built. A new generation is `bus.fresh()` — everything the code
registered on it — with the stored use cases on top, built beside the one answering,
checked by building it, and swapped in with one assignment; a request reads `current` once and
runs on that bus to the end, so a swap never lands in the middle of one. A generation that is
refused leaves `current`, and the modules it answers with, as they were. The bus given is never
built nor changed here; every generation shares its dependencies and its observability.
"""

import linecache
import sys
import threading
from collections.abc import Iterable
from types import ModuleType
from typing import Any

from sincpro_framework.runtime_use_cases.domain import RuntimeUseCase, UseCaseStore
from sincpro_framework.runtime_use_cases.loading import load, module_name
from sincpro_framework.use_bus import UseFramework


class BusRegistry:
    def __init__(self, bus: UseFramework, store: UseCaseStore) -> None:
        """`bus` is the context's bus as the code declares it — each generation starts from its
        `fresh()`."""
        self.bus = bus
        self.context = bus.name
        self.store = store
        self.generation = 0
        self._lock = threading.Lock()
        self._loaded: frozenset[RuntimeUseCase] | None = None
        self._modules: dict[str, ModuleType] = {}
        self.current: UseFramework
        self.reload()

    def _settle_modules(self, touched: Iterable[str]) -> None:
        """`sys.modules` and `linecache` hold the stored modules `current` answers with, and none
        of the others this registry `touched`."""
        in_force = {module.__file__ for module in self._modules.values()}
        for name in touched:
            module = sys.modules.pop(name, None)
            if module is not None and module.__file__ not in in_force:
                linecache.cache.pop(module.__file__ or "", None)
        sys.modules.update(self._modules)

    def _built(
        self, use_cases: list[RuntimeUseCase]
    ) -> tuple[UseFramework, dict[str, ModuleType]]:
        names = [module_name(self.context, use_case) for use_case in use_cases]
        bus = self.bus.fresh()
        try:
            for use_case in use_cases:
                load(bus, self.context, use_case)
            bus.build_root_bus()
        except Exception:
            self._settle_modules(names)
            raise
        return bus, {name: sys.modules[name] for name in names}

    def reload(self) -> bool:
        """Swap in a generation with the store's active use cases — `False`, and no build, when
        they are the ones loaded. Raises `UseCaseRefused`, with `current` as it was."""
        with self._lock:
            use_cases = self.store.active()
            if frozenset(use_cases) == self._loaded:
                return False
            bus, modules = self._built(use_cases)
            retired = self._modules.keys() - modules.keys()
            self._modules = modules
            self._settle_modules(retired)
            self._loaded = frozenset(use_cases)
            self.current = bus
            self.generation += 1
            return True

    def check(self, use_case: RuntimeUseCase) -> None:
        """Build the generation `use_case` would join, and let it go — before it is saved. Raises
        `UseCaseRefused`; the store and `current` are not touched either way."""
        with self._lock:
            active = self.store.active()
            use_cases = [use_case if one.name == use_case.name else one for one in active]
            if use_case.name not in {one.name for one in active}:
                use_cases.append(use_case)
            self._built(use_cases)
            self._settle_modules(module_name(self.context, one) for one in use_cases)

    def execute(self, dto_name: str, payload: str | dict[str, Any]) -> Any:
        """Build the Command registered as `dto_name` and execute it, both on one generation.

        Context: a stored Command is a new class in each generation and the bus answers a class,
        so one built against a generation is answered only by that generation's bus."""
        bus = self.current
        return bus(bus.map_to_dto_or_event(dto_name, payload))
