"""`InMemoryUseCases`: the store for tests and for a process that is told its use cases."""

from sincpro_framework.runtime.runtime_use_cases.domain import RuntimeUseCase, UseCaseStore


class InMemoryUseCases(UseCaseStore):
    """A `UseCaseStore` in a dict — one record per name, gone with the process.

    store = InMemoryUseCases()
    store.save(RuntimeUseCase(name="quote", source=source))
    store.active()   ->  [RuntimeUseCase(name="quote", ...)]
    """

    def __init__(self) -> None:
        """Start empty: nothing is active until something is saved."""
        self._kept: dict[str, RuntimeUseCase] = {}

    def active(self) -> list[RuntimeUseCase]:
        """The kept use cases not saved inactive, in the order their names were first saved —
        a dict keeps a key's first place when its value is replaced."""
        return [use_case for use_case in self._kept.values() if use_case.active]

    def save(self, use_case: RuntimeUseCase) -> None:
        """Keep `use_case` under its name, over the version kept before."""
        self._kept[use_case.name] = use_case
