"""What an execution is given before its handler runs: the values a provider derives, and the check
that what a use case declared it needs is there.

    prepare(node, IssueInvoice(), [tenant_provider])    tenant_id on the execution's node

**A provider runs once per scope.** It runs when an execution opens, has everything it `needs`, and
lacks something it `gives`; what it answers is written on that execution's node, so the executions
it runs find it there and the provider is not asked again.

**The call's language comes last.** When nothing set `lang` — the call did not carry it, no
provider gave it, no level above holds it — it is the first language the caller prefers
(`preferred_langs`, what an entrance read off `Accept-Language`). What a project says wins.

**Required is a declaration.** A use case that declared keys is refused, before its handler, when one
is missing — a call under the wrong tenant is the dangerous case. A use case that declared nothing is
never checked.
"""

from collections.abc import Sequence
from types import MappingProxyType

from sincpro_framework.context.domain import exceptions
from sincpro_framework.context.domain.keys import ContextKey, language_of
from sincpro_framework.context.domain.node import ContextNode
from sincpro_framework.context.domain.providers import REQUIRES, ContextProvider


def prepare(node: ContextNode, handler: object, providers: Sequence[ContextProvider]) -> None:
    """Fill the execution's node from the providers and check what the handler requires.

        prepare(node, IssueInvoice(), [tenant_provider])   ->  None, tenant_id on the node
        prepare(node, <requires tenant_id>, [])            ->  ContextRequired("...", ...)

    1. Each provider, in the order they were registered:
       1.1 one that gives nothing is skipped;
       1.2 what `node` sees now — what an earlier provider wrote included;
       1.3 one lacking one of its `needs`, or whose every `gives` is already there, is
           skipped: a scope already holding a token never asks for it again;
       1.4 its answer, when it gave one, written on `node`.
    2. No `lang` anywhere `node` sees, and languages the caller prefers: the first of them is
       the call's `lang`, written on `node`.
    3. The keys the handler declared with `requires_context` — read from its class first, so
       an instance and the class it was declared on answer the same.
    Final: refused with `ContextRequired` when one of them is missing; nothing declared,
       nothing checked.

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
    if (
        node.holding(ContextKey.LANG) is None
        and node.holding(ContextKey.PREFERRED_LANGS) is not None
    ):
        node.values.update({ContextKey.LANG: language_of(node.flattened())})
    required = tuple(getattr(type(handler), REQUIRES, ()) or getattr(handler, REQUIRES, ()))
    if required:
        absent = node.missing(required)
        if absent:
            raise exceptions.ContextRequired(type(handler).__name__, absent)
