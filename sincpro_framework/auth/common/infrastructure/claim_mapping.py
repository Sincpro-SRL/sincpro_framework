"""`ClaimMapping`: how a JWT's claims become an `Identity` — which claim is the subject, the
tenant, the roles, the scopes, the client; and the presets of the identity providers in use.

    ClaimMapping.keycloak(client="billing-api")   roles of the realm and of the client
    ClaimMapping.entra()                          oid, tid, roles, scp
    ClaimMapping.google()                         sub, hd

Context: a path is dotted — `realm_access.roles` reads `claims["realm_access"]["roles"]`.
Roles, scopes and permissions are sets read from a space-separated string or a list, unioned
over every path given. What the mapping does not read stays in `Identity.claims`, typed with
`claims_as`.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import ConfigDict

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Assurance, Identity, IdentityKind
from sincpro_framework.auth.common.infrastructure import jws
from sincpro_framework.sincpro_abstractions import DataTransferObject


def value_at(claims: Mapping[str, Any], path: str) -> Any:
    """The claim at dotted `path`, or `None` when any step is missing.

    value_at({"realm_access": {"roles": ["a"]}}, "realm_access.roles")   ->  ["a"]
    value_at({}, "realm_access.roles")                                  ->  None
    """
    current: Any = claims
    for step in path.split("."):
        if not isinstance(current, Mapping) or step not in current:
            return None
        current = current[step]
    return current


class ClaimTest(DataTransferObject):
    """A test on one claim — equal to a value, or starting with a prefix."""

    model_config = ConfigDict(frozen=True)

    path: str
    equals: str | None = None
    prefix: str | None = None

    def matches(self, claims: Mapping[str, Any]) -> bool:
        """Whether the claim at `path` is text that equals `equals` or starts with `prefix`."""
        value = value_at(claims, self.path)
        if not isinstance(value, str):
            return False
        if self.equals is not None:
            return value == self.equals
        return self.prefix is not None and value.startswith(self.prefix)


class ClaimMapping(DataTransferObject):
    """Which claims say who the caller is — the defaults are RFC 9068's, an OAuth access token's."""

    model_config = ConfigDict(frozen=True)

    subject: str = "sub"
    subject_prefix: str = "user:"
    """Put before the subject claim — `user:<sub>` — so a user and a service never share one."""
    service_prefix: str = "service:"
    service_match: ClaimTest | None = None
    """What makes a token a service's rather than a person's — Keycloak's service accounts,
    Entra's app-only tokens. It is then a SERVICE identity, `service:<sub>`."""
    tenant: str | None = None
    roles: tuple[str, ...] = ("roles",)
    scopes: tuple[str, ...] = ("scope", "scp")
    permissions: tuple[str, ...] = ()
    """Claims holding permissions themselves — a token minted by a service that resolved them."""
    client: tuple[str, ...] = ("azp", "client_id")
    refused_types: frozenset[str] = frozenset()
    """Values of the `typ` claim never accepted as an access token — Keycloak's ID and refresh
    tokens are JWTs signed with the same keys."""

    @classmethod
    def keycloak(cls, client: str | None = None, tenant: str | None = None) -> "ClaimMapping":
        """Keycloak's access tokens: the realm's roles and, given `client`, that client's; a
        service account (`preferred_username` = `service-account-…`) is a SERVICE identity; an
        ID or refresh token is refused.

            ClaimMapping.keycloak(client="billing-api").roles
                ->  ("realm_access.roles", "resource_access.billing-api.roles")
        """
        roles = ("realm_access.roles",) + (
            (f"resource_access.{client}.roles",) if client else ()
        )
        return cls(
            roles=roles,
            scopes=("scope",),
            client=("azp",),
            tenant=tenant,
            service_match=ClaimTest(path="preferred_username", prefix="service-account-"),
            refused_types=frozenset({"ID", "Refresh", "Logout"}),
        )

    @classmethod
    def entra(cls) -> "ClaimMapping":
        """Microsoft Entra ID: the object id is the subject (`sub` differs per application), the
        tenant id the tenant, app roles in `roles`, delegated scopes in `scp`; an app-only token
        (`idtyp` = `app`) is a SERVICE identity."""
        return cls(
            subject="oid",
            tenant="tid",
            roles=("roles",),
            scopes=("scp",),
            client=("azp", "appid"),
            service_match=ClaimTest(path="idtyp", equals="app"),
        )

    @classmethod
    def google(cls) -> "ClaimMapping":
        """Google: `sub`, the Workspace domain (`hd`) as the tenant, no roles."""
        return cls(tenant="hd", roles=(), scopes=("scope",), client=("azp",))

    def _names(self, claims: Mapping[str, Any], paths: tuple[str, ...]) -> frozenset[str]:
        """The union of the names held at each path."""
        return frozenset().union(*(jws.claim_names(value_at(claims, path)) for path in paths))

    def _first_text(self, claims: Mapping[str, Any], paths: tuple[str, ...]) -> str | None:
        """The first path holding text."""
        return next(
            (value for path in paths if isinstance(value := value_at(claims, path), str)),
            None,
        )

    def identity(self, claims: Mapping[str, Any], provider: str) -> Identity:
        """The identity `claims` describe, vouched for by `provider`.

        1. A service when `service_match` matches: `service:<subject>`, else `user:<subject>`;
           no subject — the claim missing, empty, or not text nor a number: `Unauthenticated`,
           never a `user:None` every such token would share.
        2. Tenant, roles, scopes, permissions and client from their paths.
        Final: session (`sid`), token id (`jti`), `auth_time`, `exp`, `acr` and `amr` typed;
        every claim retained in `claims`.
        """
        named = value_at(claims, self.subject)
        if isinstance(named, bool) or not isinstance(named, (str, int)) or named == "":
            raise exceptions.Unauthenticated(f"the token names no subject ({self.subject})")
        service = self.service_match is not None and self.service_match.matches(claims)
        prefix = self.service_prefix if service else self.subject_prefix
        tenant = value_at(claims, self.tenant) if self.tenant else None
        level = claims.get("acr")
        return Identity(
            subject=f"{prefix}{named}",
            kind=IdentityKind.SERVICE if service else IdentityKind.USER,
            tenant=None if tenant is None else str(tenant),
            roles=self._names(claims, self.roles),
            scopes=self._names(claims, self.scopes),
            permissions=self._names(claims, self.permissions),
            client=self._first_text(claims, self.client),
            provider=provider,
            claims=dict(claims),
            session=claims.get("sid") if isinstance(claims.get("sid"), str) else None,
            token_id=claims.get("jti") if isinstance(claims.get("jti"), str) else None,
            authenticated_at=jws.claim_datetime(claims, "auth_time"),
            expires_at=jws.claim_datetime(claims, "exp"),
            assurance=Assurance(
                level=level if isinstance(level, str) else None,
                methods=jws.claim_names(claims.get("amr")),
            ),
        )
