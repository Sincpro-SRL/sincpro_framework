"""`AuthProvider`: the one contract a way of authenticating and authorizing implements — a JWT
issuer, an API key table, Odoo, a policy engine, a relationship store.

    class OdooProvider(AuthProvider):
        name = "odoo"

        def authenticate(self, credentials: Credentials) -> Identity | None:
            if credentials.bearer is None:
                return None                              # not mine: the next provider is tried
            ...

Context: one method is required, `authenticate`; every other has a default that holds for the
common case and is overridden only when the protocol needs it. An abstract class, like
`KeyValueStore`: a provider of the project's proves itself with
`sincpro_framework.runtime.testing.AuthProviderContract`. Synchronous, like the bus — an entrypoint runs
the bus on a worker thread, so a provider that calls the network blocks that thread only.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.domain.exceptions import PermissionDenied
from sincpro_framework.auth.domain.identity import Credentials, Identity

if TYPE_CHECKING:
    from sincpro_framework.ddd.criteria import Criteria


class AuthProvider(ABC):
    name: str = "provider"
    """What `Identity.provider`, the logs and `describe()` call it."""

    needs_body: bool = False
    """Whether the entrypoint has to hand it the raw body — an HMAC signature over it."""

    @abstractmethod
    def authenticate(self, credentials: Credentials) -> Identity | None:
        """Who the credentials say is calling. `None` when they are not this provider's kind —
        the next provider is tried; raise `Unauthenticated` when they are, and fail."""

    def has_permission(
        self,
        identity: Identity,
        permission: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        """Whether `identity` may `permission` — on `resource` once it is loaded, with the
        request's `context`. `False`, or raise `PermissionDenied` to say why. By default the
        permissions resolved at authentication are the answer."""
        return permission in identity.permissions

    def permitted(
        self,
        identity: Identity,
        permission: str,
        resources: Sequence[Any],
        context: Mapping[str, Any] | None = None,
    ) -> list[bool]:
        """`has_permission` for many resources at once — overridden by a provider that answers
        a batch in one call, a relationship store. A resource refused with `PermissionDenied` is
        `False`, so one refusal never takes the batch down."""
        return [
            self._permits(identity, permission, resource, context) for resource in resources
        ]

    def _permits(
        self,
        identity: Identity,
        permission: str,
        resource: Any,
        context: Mapping[str, Any] | None,
    ) -> bool:
        try:
            return self.has_permission(identity, permission, resource, context)
        except PermissionDenied:
            return False

    def scope(self, identity: Identity, aggregate: type) -> "Criteria | None":
        """Which records of `aggregate` the identity may read, as a filter the repository
        applies — `None` for all of them."""
        return None

    def security_scheme(self) -> dict[str, Any] | None:
        """How this provider is described in an OpenAPI document — `{"type": "http", "scheme":
        "bearer"}`, an `apiKey` in a header — or `None` to leave it undescribed."""
        return None

    def challenge(self) -> str | None:
        """The `WWW-Authenticate` value an entrypoint answers a 401 with — where to get a
        credential this provider accepts (RFC 9728 for MCP)."""
        return None

    def credentials_for(self, identity: Identity) -> Credentials | None:
        """What to send another service so it knows who this call acts for — a signed token,
        an exchanged one (RFC 8693). `None` when this provider does not issue any."""
        return None
