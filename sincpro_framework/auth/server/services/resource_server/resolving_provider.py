"""`ResolvingProvider`: a provider of the project's whose identities go through resolvers — the
same `resolvers=` every provider the framework ships takes, for one it does not.

    auth = AccessControl(providers=[
        ResolvingProvider(OdooSessions(...), GrantResolver(catalog, grants)),
    ])

Context: a decorator over the wrapped provider (`DelegatingProvider`): it preserves its name — the
identity says it vouched — and is asked everything as before; only what `authenticate` answers
is resolved further. Resolvers change who is calling, never how a permission is judged.
"""

from sincpro_framework.auth.common.domain.identity import Credentials, Identity
from sincpro_framework.auth.common.domain.provider import AuthProvider, DelegatingProvider
from sincpro_framework.auth.common.domain.resolver import IdentityResolver, resolved


class ResolvingProvider(DelegatingProvider):
    """`provider`, its identities resolved by `resolvers` in order."""

    def __init__(self, provider: AuthProvider, *resolvers: IdentityResolver) -> None:
        super().__init__(provider)
        self.resolvers = resolvers

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """The provider's answer, resolved — `None` and refusals as the provider gave them."""
        identity = self.provider.authenticate(credentials)
        return None if identity is None else resolved(identity, self.resolvers)
