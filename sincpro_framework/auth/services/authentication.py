"""Who is calling: the providers of an `AccessControl` asked in order, and which provider answers
about an identity once it is in play.

Context: an identity names the provider that vouched for it (`Identity.provider`). A bounded
context reached in process — a bus injected as a dependency, guarded by its own `AccessControl`
— is asked about the same identity: it answers with its own provider of that name when it holds
one, else with the one of the process that let the caller in, else with the identity's own
permissions, as for an identity opened by hand. Nothing is refused for being vouched for
elsewhere: every identity in play was verified by this process or opened by its code.
"""

from collections.abc import Iterable, Sequence

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Credentials, Identity
from sincpro_framework.auth.common.domain.provider import AuthProvider
from sincpro_framework.auth.common.infrastructure.security_context import access_in_play
from sincpro_framework.auth.services.guard import guarded_buses


def _named(providers: Iterable[AuthProvider], name: str) -> AuthProvider | None:
    """The first of `providers` called `name`."""
    return next((one for one in providers if one.name == name), None)


def vouched_by(providers: Sequence[AuthProvider], identity: Identity) -> AuthProvider | None:
    """The provider asked about `identity` — `None` when its own permissions answer.

        # sales let ana in with its "jwt"; billing, reached in process, holds none of that name
        vouched_by(billing_providers, ana)   ->  sales' "jwt"
        vouched_by(providers, Identity.user("ana", permissions={...}))   ->  None

    1. Opened by hand — no provider named: `None`.
    2. One of `providers` of that name: the bounded context's own.
    3. The one of that name of the `AccessControl` in play — the use case that called this
       one — then of every other `AccessControl` guarding a bus of the process.
    Final: `None` — an identity no provider of the process vouched for answers with what it
    holds, as one opened by hand.
    """
    if identity.provider is None:
        return None
    own = _named(providers, identity.provider)
    if own is not None:
        return own
    in_play = access_in_play()
    elsewhere = ([in_play] if in_play is not None else []) + list(guarded_buses.values())
    return next(
        (
            found
            for access in elsewhere
            if (found := _named(access.providers, identity.provider)) is not None
        ),
        None,
    )


def clashing_names(providers: Sequence[AuthProvider]) -> set[str]:
    """The names of `providers` that another `AccessControl` of the process gives to a provider
    of another kind — an identity vouched for under that name may be asked of either.

        # sales holds StaticProvider "static"; billing holds its own OdooKeys named "static"
        clashing_names(billing_providers)   ->  {"static"}

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


def authenticated(providers: Sequence[AuthProvider], credentials: Credentials) -> Identity:
    """Who the credentials say is calling — the first provider that recognizes them, or
    `Identity.anonymous()` when none does. `Unauthenticated` when one recognizes them and
    they fail.

        # providers=[jwt, api_keys]
        x-api-key: sk_3f9…      ->  Identity("apikey:ops-bot", provider="api-keys")
        no credentials at all   ->  Identity.anonymous()
        Authorization: Bearer <expired>  ->  raises Unauthenticated, api_keys not asked

    Context: the identity is stamped with the name of the provider that answered, even when
    the provider left `provider` unset — that is the one later asked about it.
    """
    for provider in providers:
        identity = provider.authenticate(credentials)
        if identity is not None:
            if identity.provider != provider.name:
                identity = identity.model_copy(update={"provider": provider.name})
            return identity
    return Identity.anonymous()


def challenges(
    providers: Sequence[AuthProvider], refusal: exceptions.Unauthenticated | None
) -> list[str]:
    """The `WWW-Authenticate` values a 401 answers with, one per provider that has one — for
    `refusal` when the wire knows it, so a step-up names what to come back with."""
    return [challenge for one in providers if (challenge := one.challenge_for(refusal))]


def credentials_for(
    providers: Sequence[AuthProvider], identity: Identity, audience: str | None
) -> Credentials | None:
    """What to send another service — `audience`, when named — so it knows who this call acts
    for, issued by one of `providers`.

        # acting as user:42, vouched for by "service-tokens"
        credentials_for(providers, user_42, "sales")
            ->  Credentials(transport="service", headers={"x-sp-service-token": "eyJ…"})
        credentials_for(providers, Identity.anonymous(), None)   ->  None

    1. An identity vouched for by a provider none of `providers` is: `None` — another
       `AccessControl` of the process issues for it (`security.identity_headers` asks each).
    2. The identity's own provider first, then the others in order.
    Final: the first that issues something; else `None`.
    """
    if identity.provider is not None and _named(providers, identity.provider) is None:
        return None
    own = vouched_by(providers, identity)
    for provider in (own, *(one for one in providers if one is not own)):
        if provider is not None and (issued := provider.outbound_for(identity, audience)):
            return issued
    return None
