"""Runtime use cases: Commands, Responses and a Feature or ApplicationService stored as source,
loaded onto the bus while the service runs.

    registry = BusRegistry(billing, store)                        # the code's bus + what is stored
    registry.check(draft)                                         # refused before it is saved
    store.save(draft)
    registry.reload()                                             # a new generation, swapped in whole
    registry.execute("sincpro_runtime.billing.quote.CommandQuote", {"amount": 100})

The source is the only truth about a stored use case — it is written, reviewed and tested as any
Python module, and it answers through the same bus, interceptors and observability as the code.
A stored handler may `replaces=` one in code, as a Feature in code does. The core ships the
store contract and a store in memory; `sincpro_framework.orm.runtime_use_cases` a table.
"""

from sincpro_framework.runtime_use_cases.domain import (
    RuntimeUseCase,
    UseCaseRefused,
    UseCaseStore,
)
from sincpro_framework.runtime_use_cases.in_memory import InMemoryUseCases
from sincpro_framework.runtime_use_cases.registry import BusRegistry

__all__ = [
    "BusRegistry",
    "InMemoryUseCases",
    "RuntimeUseCase",
    "UseCaseRefused",
    "UseCaseStore",
]
