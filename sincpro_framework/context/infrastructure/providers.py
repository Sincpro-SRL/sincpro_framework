"""What an execution is given before its handler runs: the values a provider derives, and the check
that what a use case declared it needs is there.

    @siat_soap_sdk.context_provider(needs=["nit_id"], gives=["TOKEN", "SIAT_ENV"])
    def siat_credentials(context):
        nit = nits.get(context["nit_id"])
        return {"TOKEN": nit.token, "SIAT_ENV": nit.environment}

    @siat_soap_sdk.feature(CommandSendDocument)
    @requires_context("TOKEN", "SIAT_ENV")
    class SendDocument(Feature): ...

**A provider runs once per scope.** It runs when an execution opens, has everything it `needs`, and
lacks something it `gives`; what it answers is written on that execution's node, so the executions
it runs find it there and the provider is not asked again.

**Required is a declaration.** A use case that declared keys is refused, before its handler, when one
is missing — a call under the wrong tenant is the dangerous case. A use case that declared nothing is
never checked.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sincpro_framework.context.domain.keys import standardized
from sincpro_framework.context.domain.node import ContextNode

REQUIRES = "__requires_context__"

type ContextProviderFunction = Callable[[Mapping[Any, Any]], Mapping[Any, Any] | None]


@dataclass(frozen=True)
class ContextProvider:
    function: ContextProviderFunction
    needs: tuple[str, ...]
    gives: tuple[str, ...]

    @property
    def name(self) -> str:
        return getattr(self.function, "__qualname__", repr(self.function))


def requires_context[T](*keys: str) -> Callable[[T], T]:
    """The keys a Feature or an ApplicationService cannot run without — checked before its handler,
    refused with `ContextRequired` when one is missing."""

    def declare(handler: T) -> T:
        setattr(handler, REQUIRES, tuple(keys))
        return handler

    return declare


def required_by(handler: Any) -> tuple[str, ...]:
    return tuple(getattr(type(handler), REQUIRES, ()) or getattr(handler, REQUIRES, ()))


def provided(node: ContextNode, providers: Sequence[ContextProvider]) -> None:
    """Every provider that applies, in the order they were registered, written on `node`."""
    for provider in providers:
        if not provider.gives:
            continue
        seen = node.flattened()
        if any(need not in seen for need in provider.needs):
            continue
        if all(give in seen for give in provider.gives):
            continue
        answered = provider.function(MappingProxyType(seen))
        if answered:
            node.values.update(standardized(answered))


def missing(node: ContextNode, keys: Iterable[str]) -> list[str]:
    return [key for key in keys if node.holding(key) is None]
