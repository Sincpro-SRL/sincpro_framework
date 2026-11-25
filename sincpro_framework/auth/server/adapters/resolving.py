"""`Resolving`: a provider of the project's whose identities go through resolvers — the same
`resolvers=` every provider the framework ships takes, for one it does not.

    auth = AccessControl(providers=[Resolving(OdooSessions(...), GrantResolver(catalog, grants))])

Context: the wrapped provider keeps its name — the identity says it vouched — and is asked
everything else as before; only what `authenticate` answers is resolved further.
"""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Credentials, Identity
from sincpro_framework.auth.common.domain.provider import AuthProvider
from sincpro_framework.auth.common.domain.resolver import IdentityResolver, resolved

if TYPE_CHECKING:
    from sincpro_framework.ddd.criteria import Criteria


class Resolving(AuthProvider):
    """`provider`, its identities resolved by `resolvers` in order."""

    def __init__(self, provider: AuthProvider, *resolvers: IdentityResolver) -> None:
        """The provider and its resolvers; the provider's `name` and `needs_body` are kept."""
        self.provider = provider
        self.resolvers = resolvers
        self.name = provider.name
        self.needs_body = provider.needs_body

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """The provider's answer, resolved — `None` and refusals as the provider gave them."""
        identity = self.provider.authenticate(credentials)
        return None if identity is None else resolved(identity, self.resolvers)

    def has_permission(
        self,
        identity: Identity,
        permission: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        """The wrapped provider's — resolvers change who is calling, never how it is judged."""
        return self.provider.has_permission(identity, permission, resource, context)

    def permitted(
        self,
        identity: Identity,
        permission: str,
        resources: Sequence[Any],
        context: Mapping[str, Any] | None = None,
    ) -> list[bool]:
        """The wrapped provider's — resolvers change who is calling, never how it is judged."""
        return self.provider.permitted(identity, permission, resources, context)

    def refusing_any(
        self, identity: Identity, permissions: Sequence[str], reasons: Sequence[str]
    ) -> str | None:
        """The wrapped provider's — resolvers change who is calling, never how it is judged."""
        return self.provider.refusing_any(identity, permissions, reasons)

    def scope(self, identity: Identity, aggregate: type) -> "Criteria | None":
        """The wrapped provider's — resolvers change who is calling, never how it is judged."""
        return self.provider.scope(identity, aggregate)

    def security_scheme(self) -> dict[str, Any] | None:
        """The wrapped provider's."""
        return self.provider.security_scheme()

    def challenge(self) -> str | None:
        """The wrapped provider's."""
        return self.provider.challenge()

    def challenge_for(self, refusal: exceptions.Unauthenticated | None) -> str | None:
        """The wrapped provider's."""
        return self.provider.challenge_for(refusal)

    def credentials_for(self, identity: Identity) -> Credentials | None:
        """The wrapped provider's."""
        return self.provider.credentials_for(identity)

    def outbound_for(
        self, identity: Identity, audience: str | None = None
    ) -> Credentials | None:
        """The wrapped provider's."""
        return self.provider.outbound_for(identity, audience)
