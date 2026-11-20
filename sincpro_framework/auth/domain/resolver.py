"""`IdentityResolver`: what turns the identity a credential proved into the identity the service
acts with — its roles mapped, its permissions resolved from a store, narrowed by its scopes and
its tenant's plan. ASP.NET calls it a claims transformation.

    class TenantAgents:
        def resolve(self, identity: Identity) -> Identity:
            agents = registry.agents_of(identity.tenant)
            return identity.model_copy(update={"permissions": identity.permissions | agents})

    JwtProvider(issuers=[keycloak], resolvers=[GrantResolver(catalog, store), TenantAgents()])

Context: a resolver runs once, when a provider authenticates — so `has_permission` answers from
the identity, and a catalog of fifty use cases asks nothing more. It belongs to a provider, never
to `AccessControl`: a service token or an API key arrives with its permissions already resolved,
and a resolver applied to every identity would grant twice or narrow wrongly. `RolePermissions`
is one, unchanged.
"""

from collections.abc import Iterable
from typing import Protocol

from sincpro_framework.auth.domain.identity import Identity


class IdentityResolver(Protocol):
    """The port: the identity, resolved further — never `None`; a resolver that has nothing to
    add answers it as it came."""

    def resolve(self, identity: Identity) -> Identity: ...


def resolved(identity: Identity, resolvers: Iterable[IdentityResolver]) -> Identity:
    """`identity` through each resolver, in order — each sees what the previous one answered."""
    for resolver in resolvers:
        identity = resolver.resolve(identity)
    return identity
