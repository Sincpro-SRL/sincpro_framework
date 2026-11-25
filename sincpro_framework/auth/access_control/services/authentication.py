"""Who is calling: the providers of an `AccessControl` asked in order, on the store of the bus the
call runs on, and which provider answers about an identity once it is in play.

Context: an identity names the provider that vouched for it (`Identity.provider`). A bounded
context reached in process — a bus injected as a dependency, guarded by its own `AccessControl`
— is asked about the same identity: it answers with its own provider of that name when it holds
one, else with the one of the process that let the caller in, else with the identity's own
permissions, as for an identity opened by hand. Nothing is refused for being vouched for
elsewhere: every identity in play was verified by this process or opened by its code.
"""

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from sincpro_framework.auth.access_control.services.guarded_buses import (
    find_guarding,
    guarded_buses,
)
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Credentials, Identity
from sincpro_framework.auth.common.domain.provider import AuthProvider
from sincpro_framework.auth.common.infrastructure.security_context import (
    current_access_control,
    current_identity,
)
from sincpro_framework.context import ContextStore, use_context

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework


def find_provider(
    providers: Sequence[AuthProvider], identity: Identity
) -> AuthProvider | None:
    """The provider asked about `identity` — `None` when its own permissions answer.

        # sales let ana in with its "jwt"; billing, reached in process, holds none of that name
        find_provider(billing_providers, ana)   ->  sales' "jwt"
        find_provider(providers, Identity.user("ana", permissions={...}))   ->  None

    1. Opened by hand — no provider named: `None`.
    2. One of `providers` of that name: the bounded context's own.
    3. The one of that name of the `AccessControl` in play — the use case that called this
       one — then of every other `AccessControl` guarding a bus of the process.
    Final: `None` — an identity no provider of the process vouched for answers with what it
    holds, as one opened by hand.
    """
    if identity.provider is None:
        return None
    in_play = current_access_control()
    elsewhere = [one.providers for one in ([in_play] if in_play is not None else [])]
    elsewhere += [one.providers for one in list(guarded_buses.values())]
    return next(
        (
            provider
            for candidates in (providers, *elsewhere)
            for provider in candidates
            if provider.name == identity.provider
        ),
        None,
    )


def find_clashing_names(providers: Sequence[AuthProvider]) -> set[str]:
    """The names of `providers` that another `AccessControl` of the process gives to a provider
    of another kind — an identity vouched for under that name may be asked of either.

        # sales holds StaticProvider "static"; billing holds its own OdooKeys named "static"
        find_clashing_names(billing_providers)   ->  {"static"}

    Context: instances of one kind under one name are what several bounded contexts verifying
    the same tokens hold, and never clash.
    """
    kinds = {one.name: type(one) for one in providers}
    return {
        other.name
        for access in list(guarded_buses.values())
        for other in access.providers
        if other.name in kinds and type(other) is not kinds[other.name]
    }


def _ask_providers(providers: Sequence[AuthProvider], credentials: Credentials) -> Identity:
    """The first provider that recognizes `credentials`, its identity stamped with its name;
    `Identity.anonymous()` when none does."""
    for provider in providers:
        identity = provider.authenticate(credentials)
        if identity is not None:
            if identity.provider != provider.name:
                identity = identity.model_copy(update={"provider": provider.name})
            return identity
    return Identity.anonymous()


def authenticate(
    providers: Sequence[AuthProvider],
    credentials: Credentials,
    bus: "UseFramework | None" = None,
    guarded: Iterable["UseFramework"] = (),
) -> Identity:
    """Who the credentials say is calling — the first provider that recognizes them, or
    `Identity.anonymous()` when none does. `Unauthenticated` when one recognizes them and
    they fail.

        # providers=[jwt, api_keys]
        x-api-key: sk_3f9…      ->  Identity("apikey:ops-bot", provider="api-keys")
        no credentials at all   ->  Identity.anonymous()
        Authorization: Bearer <expired>  ->  raises Unauthenticated, api_keys not asked

    Context: the providers read what auth stores — revocations, refresh tokens, grants — on the
    store the call's bus stores its context in, where its use cases wrote it: a sign-out inside
    a bounded context with a store of its own is what its next request reads. Every door —
    `security.authenticated_as`, `IdentityMiddleware` — authenticates here, so they agree.

    1. The store: `bus`'s; without one, the first of the `guarded` buses that has one; else
       the running context's, as it is.
    2. The providers asked in order, on that store — the identity stamped with the name of the
       provider that answered, even when the provider left `provider` unset: that is the one
       later asked about it.
    Final: the identity, or `Identity.anonymous()`.
    """
    buses = [bus] if bus is not None else list(guarded)
    store: ContextStore | None = next(
        (found for one in buses if (found := one.current_context().store()) is not None), None
    )
    if store is None:
        return _ask_providers(providers, credentials)
    with use_context().scoped({ContextStore: store}):
        return _ask_providers(providers, credentials)


def build_challenges(
    providers: Sequence[AuthProvider], refusal: exceptions.Unauthenticated | None
) -> list[str]:
    """The `WWW-Authenticate` values a 401 answers with, one per provider that has one — for
    `refusal` when the wire knows it, so a step-up names what to come back with."""
    return [challenge for one in providers if (challenge := one.challenge_for(refusal))]


def issue_credentials(
    providers: Sequence[AuthProvider], identity: Identity, audience: str | None
) -> Credentials | None:
    """What to send another service — `audience`, when named — so it knows who this call acts
    for, issued by one of `providers`.

        # acting as user:42, vouched for by "service-tokens"
        issue_credentials(providers, user_42, "sales")
            ->  Credentials(transport="service", headers={"x-sp-service-token": "eyJ…"})
        issue_credentials(providers, Identity.anonymous(), None)   ->  None

    1. An identity vouched for by a provider none of `providers` is: `None` — another
       `AccessControl` of the process issues for it (`security.identity_headers` asks each).
    2. The identity's own provider first, then the others in order.
    Final: the first that issues something; else `None`.
    """
    own = next((one for one in providers if one.name == identity.provider), None)
    if identity.provider is not None and own is None:
        return None
    for provider in (own, *(one for one in providers if one is not own)):
        if provider is not None and (issued := provider.outbound_for(identity, audience)):
            return issued
    return None


def find_identity_headers(context: str) -> dict[str, str]:
    """The headers a call to the service hosting `context` carries, so it knows who the call
    acts for — what the identity's provider issues; empty when nobody is calling or no provider
    issues any.

        # acting as user:42, vouched for by a ServiceTokenProvider
        find_identity_headers("sales")   ->  {"x-sp-service-token": "eyJhbGciOiJIUzI1NiIs…"}

    1. Nobody calling: nothing.
    2. The `AccessControl` guarding the use case in progress first — it vouched for the
       identity —, then those guarding a bus of `context`, then the rest (`find_guarding`).
    Final: what the first that issues something issues; one that does not know the identity's
    provider steps aside.
    """
    identity = current_identity()
    if identity.is_anonymous:
        return {}
    in_play = current_access_control()
    asked = [in_play] if in_play is not None else []
    asked += [one for one in find_guarding(context) if one is not in_play]
    for access in asked:
        issued = issue_credentials(access.providers, identity, context)
        if issued is not None:
            return dict(issued.headers)
    return {}
