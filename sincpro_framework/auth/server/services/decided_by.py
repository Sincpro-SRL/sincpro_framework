"""`DecidedBy`: a provider's identities, decided about by a policy engine — the provider says who
calls, the engine whether they may.

    auth = AccessControl(providers=[DecidedBy(JwtProvider(keycloak), AuthZenEngine(pdp_url))])

Context: it keeps the wrapped provider's name — the identities it vouches for are asked of it —
and answers `has_permission`, `permitted` and `scope` from the engine; a `scope` the engine does
not give is the provider's.
"""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Credentials, Identity
from sincpro_framework.auth.common.domain.provider import AuthProvider
from sincpro_framework.auth.server.domain.policy import PolicyEngine

if TYPE_CHECKING:
    from sincpro_framework.ddd.criteria import Criteria


class DecidedBy(AuthProvider):
    """`provider` authenticates; `engine` decides."""

    def __init__(self, provider: AuthProvider, engine: PolicyEngine) -> None:
        self.provider = provider
        self.engine = engine
        self.name = provider.name
        self.needs_body = provider.needs_body

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """Who is calling — the provider's answer."""
        return self.provider.authenticate(credentials)

    def has_permission(
        self,
        identity: Identity,
        permission: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        """The engine's decision — the provider is not asked."""
        return self.engine.decide(identity, permission, resource, context)

    def permitted(
        self,
        identity: Identity,
        permission: str,
        resources: Sequence[Any],
        context: Mapping[str, Any] | None = None,
    ) -> list[bool]:
        """The engine's decisions — in one request when it batches."""
        return self.engine.decide_many(identity, permission, resources, context)

    def scope(self, identity: Identity, aggregate: type) -> "Criteria | None":
        """What the engine says the identity may read of `aggregate` — the provider's scope
        when the engine has none."""
        found = self.engine.scope(identity, aggregate)
        return found if found is not None else self.provider.scope(identity, aggregate)

    def security_scheme(self) -> dict[str, Any] | None:
        """The provider's — the engine decides permissions, nothing else."""
        return self.provider.security_scheme()

    def challenge(self) -> str | None:
        """The provider's — the engine decides permissions, nothing else."""
        return self.provider.challenge()

    def challenge_for(self, refusal: exceptions.Unauthenticated | None) -> str | None:
        """The provider's — the engine decides permissions, nothing else."""
        return self.provider.challenge_for(refusal)

    def credentials_for(self, identity: Identity) -> Credentials | None:
        """The provider's — the engine decides permissions, nothing else."""
        return self.provider.credentials_for(identity)

    def outbound_for(
        self, identity: Identity, audience: str | None = None
    ) -> Credentials | None:
        """The provider's — the engine decides permissions, nothing else."""
        return self.provider.outbound_for(identity, audience)
