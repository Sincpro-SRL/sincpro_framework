"""`ContextProvider`: what gives an execution values it derives from its context — the function, the
keys it needs and the keys it gives — and the mark a use case declares the keys it requires with.

    ContextProvider(siat_credentials, needs=("nit_id",), gives=("TOKEN", "SIAT_ENV"))
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

REQUIRES = "__requires_context__"
"""The attribute `requires_context` marks a Feature or an ApplicationService with."""

type ContextProviderFunction = Callable[[Mapping[Any, Any]], Mapping[Any, Any] | None]


@dataclass(frozen=True)
class ContextProvider:
    """One registered provider: the function, the keys it needs and the keys it gives — what
    `bus.context_provider(needs, gives)` records."""

    function: ContextProviderFunction
    needs: tuple[str, ...]
    gives: tuple[str, ...]

    @property
    def name(self) -> str:
        """The function's qualified name — what a log or an error names the provider by."""
        return getattr(self.function, "__qualname__", repr(self.function))
