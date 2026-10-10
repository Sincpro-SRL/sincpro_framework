"""`InMemoryUseCases`: the store for tests and for a process that is told its use cases."""

from sincpro_framework.runtime.runtime_use_cases.domain import RuntimeUseCase, UseCaseStore


class InMemoryUseCases(UseCaseStore):
    def __init__(self) -> None:
        self._kept: dict[str, RuntimeUseCase] = {}

    def active(self) -> list[RuntimeUseCase]:
        return [use_case for use_case in self._kept.values() if use_case.active]

    def save(self, use_case: RuntimeUseCase) -> None:
        self._kept[use_case.name] = use_case
