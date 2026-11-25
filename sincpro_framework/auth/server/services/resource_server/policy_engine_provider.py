"""`PolicyEngineProvider`: a provider's identities, decided about by a policy engine — the
provider authenticates (who calls), the engine authorizes (whether they may): a Policy Decision
Point such as AuthZEN, OPA or OpenFGA.

    auth = AccessControl(providers=[
        PolicyEngineProvider(JwtProvider(keycloak), AuthZenEngine(pdp_url)),
    ])

Context: a decorator over the wrapped provider (`DelegatingProvider`): it preserves its name — the
identities it vouches for are asked of it — and answers `has_permission`, `permitted` and
`scope` from the engine; a `scope` the engine does not give is the provider's. Everything else —
the challenge, the security scheme, outbound credentials — is the provider's.
"""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.common.domain.identity import Identity
from sincpro_framework.auth.common.domain.provider import AuthProvider, DelegatingProvider
from sincpro_framework.auth.server.domain.resource_server.policy import PolicyEngine

if TYPE_CHECKING:
    from sincpro_framework.ddd.criteria import Criteria


class PolicyEngineProvider(DelegatingProvider):
    """`provider` authenticates; `engine` decides."""

    def __init__(self, provider: AuthProvider, engine: PolicyEngine) -> None:
        super().__init__(provider)
        self.engine = engine

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

    def refusing_any(
        self, identity: Identity, permissions: Sequence[str], reasons: Sequence[str]
    ) -> str | None:
        """The reasons joined — the engine decided each permission, the provider none."""
        return None

    def scope(self, identity: Identity, aggregate: type) -> "Criteria | None":
        """What the engine says the identity may read of `aggregate` — the provider's scope
        when the engine has none."""
        found = self.engine.scope(identity, aggregate)
        return found if found is not None else self.provider.scope(identity, aggregate)
