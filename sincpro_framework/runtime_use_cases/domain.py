"""The vocabulary: a use case stored as source, and the contract of where it is stored."""

import hashlib
from abc import ABC, abstractmethod

from pydantic import ConfigDict

from sincpro_framework.deprecations import PositionalFields
from sincpro_framework.sincpro_abstractions import DataTransferObject


class UseCaseRefused(Exception):
    """A stored use case that cannot be loaded — the bus it would have joined is left as it was."""


class RuntimeUseCase(PositionalFields, DataTransferObject):
    """A DTO: it is kept in a store and arrives from outside the process, as an HTTP body."""

    model_config = ConfigDict(frozen=True)

    name: str
    """Unique in its bounded context; its module is `sincpro_runtime.<context>.<name>`, so its
    Commands are routed as `sincpro_runtime.<context>.<name>.<Class>`."""

    source: str
    """A Python module: its Commands, its Responses and the one Feature or ApplicationService
    that answers them — the only truth about the use case."""

    version: int = 1
    active: bool = True

    replaces: str | None = None
    """`module.Class` of the handler in code it answers instead of, as `replaces=` does."""

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.source.encode()).hexdigest()[:16]


class UseCaseStore(ABC):
    """Where use cases are kept — a table, a document store, memory. One record per name: saving
    a use case again is its new version."""

    @abstractmethod
    def active(self) -> list[RuntimeUseCase]:
        """Every use case to load, in the order they were first saved."""

    @abstractmethod
    def save(self, use_case: RuntimeUseCase) -> None:
        """Keep `use_case` as the one under its name."""
