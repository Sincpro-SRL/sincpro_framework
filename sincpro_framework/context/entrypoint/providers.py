"""`requires_context`: the keys a Feature or an ApplicationService cannot run without.

    @siat_soap_sdk.context_provider(needs=["nit_id"], gives=["TOKEN", "SIAT_ENV"])
    def siat_credentials(context):
        nit = nits.get(context["nit_id"])
        return {"TOKEN": nit.token, "SIAT_ENV": nit.environment}

    @siat_soap_sdk.feature(CommandSendDocument)
    @requires_context("TOKEN", "SIAT_ENV")
    class SendDocument(Feature): ...

What a provider gives and what a use case requires are prepared before its handler, at each run
(`sincpro_framework.context.services.providers`).
"""

from collections.abc import Callable

from sincpro_framework.context.domain.providers import REQUIRES


def requires_context[T](*keys: str) -> Callable[[T], T]:
    """The keys a Feature or an ApplicationService cannot run without — checked before its handler,
    refused with `ContextRequired` when one is missing.

        @requires_context("TOKEN", "SIAT_ENV")
        class SendDocument(Feature): ...
        SendDocument.__requires_context__   ->  ("TOKEN", "SIAT_ENV")

    The decorator only marks the class (`__requires_context__`) and answers it unchanged;
    the bus checks the keys at each run."""

    def declare(handler: T) -> T:
        """Mark `handler` with the keys — read back when the bus prepares an execution."""
        setattr(handler, REQUIRES, tuple(keys))
        return handler

    return declare
