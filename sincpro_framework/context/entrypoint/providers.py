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

from sincpro_framework.context.domain.node import ContextNode

REQUIRES = "__requires_context__"

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


def requires_context[T](*keys: str) -> Callable[[T], T]:
    """The keys a Feature or an ApplicationService cannot run without — checked before its handler,
    refused with `ContextRequired` when one is missing.

        @requires_context("TOKEN", "SIAT_ENV")
        class SendDocument(Feature): ...
        required_by(SendDocument())   ->  ("TOKEN", "SIAT_ENV")

    The decorator only marks the class (`__requires_context__`) and answers it unchanged;
    the bus checks the keys at each run."""

    def declare(handler: T) -> T:
        """Mark `handler` with the keys — read back by `required_by`."""
        setattr(handler, REQUIRES, tuple(keys))
        return handler

    return declare


def required_by(handler: Any) -> tuple[str, ...]:
    """The keys `handler` declared with `requires_context` — read from its class first, so an
    instance and the class it was declared on answer the same; `()` when it declared none."""
    return tuple(getattr(type(handler), REQUIRES, ()) or getattr(handler, REQUIRES, ()))


def provided(node: ContextNode, providers: Sequence[ContextProvider]) -> None:
    """Every provider that applies, in the order they were registered, written on `node`.

    1. A provider that gives nothing is skipped.
    2. What `node` sees now — what an earlier provider wrote included.
    3. A provider lacking one of its `needs`, or whose every `gives` is already there, is
       skipped: a scope already holding a token never asks for it again.
    Final: its answer, when it gave one, written on `node`.

        needs=["nit_id"], gives=["TOKEN"]   context {"nit_id": 7}               runs
        needs=["nit_id"], gives=["TOKEN"]   context {"nit_id": 7, "TOKEN": "t"} skipped
    """
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
            node.values.update(answered)


def missing(node: ContextNode, keys: Iterable[str]) -> list[str]:
    """The keys `node` does not see — a key a scope took away counts as missing: what
    refuses a use case with `ContextRequired`.

        missing(node, ["tenant_id", "TOKEN"])  ->  ["TOKEN"]     (the context says tenant_id)
    """
    return [key for key in keys if node.holding(key) is None]
