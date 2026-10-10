"""`BusRegistry`: the bus of a bounded context, as the code declares it plus the use cases stored
for it — rebuilt as a new generation when they change, and swapped in whole.

Context: a bus is not changed once built. A new generation is `bus.fresh()` — everything the code
registered on it — with the stored use cases on top, built beside the one answering,
checked by building it, and swapped in with one assignment; a request reads `current` once and
runs on that bus to the end, so a swap never lands in the middle of one. A generation that is
refused leaves `current`, and the modules it answers with, as they were. The bus given is never
built nor changed here; every generation shares its dependencies and its observability. The
first generation is built on first use, not when the registry is made: made at import, it would
read a store the migrations have not created yet.
"""

import linecache
import sys
import threading
from collections.abc import Iterable
from types import ModuleType
from typing import Any

from sincpro_framework.runtime.runtime_use_cases.domain import (
    RuntimeUseCase,
    UseCaseRefused,
    UseCaseStore,
)
from sincpro_framework.runtime.runtime_use_cases.loading import load, module_name
from sincpro_framework.use_bus import UseFramework


def _joined(active: list[RuntimeUseCase], use_case: RuntimeUseCase) -> list[RuntimeUseCase]:
    """The use cases in force once `use_case` is saved: in its place when its name is stored,
    last when it is new, and out when it is saved inactive."""
    stored = {one.name for one in active}
    joined = [use_case if one.name == use_case.name else one for one in active]
    if use_case.name not in stored:
        joined.append(use_case)
    return [one for one in joined if one.active]


class BusRegistry:
    def __init__(self, bus: UseFramework, store: UseCaseStore) -> None:
        """`bus` is the context's bus as the code declares it — each generation starts from its
        `fresh()`. Nothing is read nor built until the first `current`, `execute` or `reload`.
        """
        self.bus = bus
        self.context = bus.name
        self.store = store
        self.generation = 0
        self._lock = threading.Lock()
        self._loaded: tuple[RuntimeUseCase, ...] = ()
        self._modules: dict[str, ModuleType] = {}
        self._current: UseFramework | None = None

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

    def _swapped_in(
        self,
        bus: UseFramework,
        modules: dict[str, ModuleType],
        use_cases: list[RuntimeUseCase],
    ) -> UseFramework:
        """Context: called under the lock, with a generation already built."""
        retired = self._modules.keys() - modules.keys()
        self._modules = modules
        self._settle_modules(retired)
        self._loaded = tuple(use_cases)
        self._current = bus
        self.generation += 1
        return bus

    def _first_generation(self) -> UseFramework:
        with self._lock:
            if self._current is not None:
                return self._current
            use_cases = self.store.active()
            return self._swapped_in(*self._built(use_cases), use_cases)

    @property
    def current(self) -> UseFramework:
        """The bus answering — built from the store on the first call, read with no lock after.
        Raises `UseCaseRefused` while the first generation does not load."""
        current = self._current
        if current is None:
            return self._first_generation()
        return current

    @property
    def in_force(self) -> tuple[RuntimeUseCase, ...]:
        """The stored use cases `current` answers with, in the order they load — empty before the
        first generation. Context: after a refused `reload` this is the generation still
        serving, not what the store holds."""
        return self._loaded

    def reload(self) -> bool:
        """Swap in a generation with the store's active use cases — `False`, and no build, when
        they are the ones loaded. Raises `UseCaseRefused`, with `current` as it was."""
        with self._lock:
            use_cases = self.store.active()
            if self._current is not None and frozenset(use_cases) == frozenset(self._loaded):
                return False
            self._swapped_in(*self._built(use_cases), use_cases)
            return True

    def check(self, use_case: RuntimeUseCase) -> None:
        """Build the generation `use_case` would join, and let it go — before it is saved. Raises
        `UseCaseRefused`; the store and `current` are not touched either way."""
        with self._lock:
            use_cases = _joined(self.store.active(), use_case)
            self._built(use_cases)
            self._settle_modules(module_name(self.context, one) for one in use_cases)

    def put(self, use_case: RuntimeUseCase) -> None:
        """Check, save and swap in, as one step no other registry call interleaves with.

        1. Build the generation `use_case` joins; `UseCaseRefused` saves nothing.
        2. Save it; a store that fails leaves `current` as it was.
        3. Final: swap in the generation built in 1.
        """
        with self._lock:
            use_cases = _joined(self.store.active(), use_case)
            bus, modules = self._built(use_cases)
            try:
                self.store.save(use_case)
            except Exception:
                self._settle_modules(modules)
                raise
            self._swapped_in(bus, modules, use_cases)

    def check_all(self) -> list[UseCaseRefused]:
        """Every active stored use case loaded against the code as it is now, and the ones that
        do not load — what CI runs, as `migrations check` guards the schema. Nothing is swapped
        in; an empty list is a store the code still answers.

        Context: each use case loads beside the ones before it, so one that imports a refused one
        is refused too, in its own name."""
        with self._lock:
            use_cases = self.store.active()
            bus = self.bus.fresh()
            refusals: list[UseCaseRefused] = []
            for use_case in use_cases:
                try:
                    load(bus, self.context, use_case)
                except UseCaseRefused as refused:
                    refusals.append(refused)
            if not refusals:
                try:
                    bus.build_root_bus()
                except Exception as error:
                    refusals.append(UseCaseRefused(f"the generation: {error}"))
            self._settle_modules(module_name(self.context, one) for one in use_cases)
            return refusals

    def execute(self, dto_name: str, payload: str | dict[str, Any]) -> Any:
        """Build the Command registered as `dto_name` — its identity, or its class name — and
        execute it, both on one generation.

        Context: a stored Command is a new class in each generation and the bus answers a class,
        so one built against a generation is answered only by that generation's bus."""
        bus = self.current
        return bus(bus.map_to_dto_or_event(dto_name, payload))
