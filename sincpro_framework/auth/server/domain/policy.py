"""`PolicyEngine`: an authorization decision taken outside the service — by a policy engine (OPA,
Cedar, Cerbos, any AuthZEN PDP) or a relationship store (OpenFGA, SpiceDB) — in OpenID AuthZEN's
terms: a subject, an action, a resource, a context.

    DecidedBy(JwtProvider(keycloak), OpaEngine("https://opa.internal", "billing/allow"))

Context: an engine authenticates nobody; it decides about the identities a provider vouched for —
so it is put beside that provider (`DecidedBy`), whose identities it then answers about. An engine
that does not answer is `EndpointUnavailable` (503) — never `False`: a refusal the engine did not
make would be a lie the caller could act on.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.common.domain.identity import Identity

if TYPE_CHECKING:
    from sincpro_framework.ddd.criteria import Criteria


class PolicyEngine(ABC):
    """The port: may `identity` do `permission` on `resource`, in `context`."""

    @abstractmethod
    def decide(
        self,
        identity: Identity,
        permission: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        """The engine's decision for one resource."""

    def decide_many(
        self,
        identity: Identity,
        permission: str,
        resources: Sequence[Any],
        context: Mapping[str, Any] | None = None,
    ) -> list[bool]:
        """One decision per resource — in one call when the engine answers batches."""
        return [self.decide(identity, permission, one, context) for one in resources]

    def scope(self, identity: Identity, aggregate: type) -> "Criteria | None":
        """Which records of `aggregate` the identity may read, when the engine can list them."""
        return None
