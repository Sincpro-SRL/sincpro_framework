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
        self._stored: dict[str, RuntimeUseCase] = {}

    def active(self) -> list[RuntimeUseCase]:
        """The stored use cases not saved inactive, in the order their names were first saved
        — a dict preserves a key's first place when its value is replaced."""
        return [use_case for use_case in self._stored.values() if use_case.active]

    def save(self, use_case: RuntimeUseCase) -> None:
        """Store `use_case` under its name, over the version stored before."""
        self._stored[use_case.name] = use_case
