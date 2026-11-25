"""`GrantResolver`: the identity a credential proved, with the permissions it holds here — the
roles its token carries mapped to the catalog's, the roles assigned in the store, their closure,
what they grant, gated by what the tenant bought and narrowed by the token's scopes.

    resolver = GrantResolver(
        catalog,
        grants,                                        a GrantStore — the roles given here
        role_mapping=RoleMapping({"app:billing": "billing.clerk"}),
        scope_ceiling=ScopeCeiling({"billing:read": ["billing.*.read"]}, first_party={"billing-web"}),
        entitlements=True,
        revocations=revocations,
    )
    JwtProvider(keycloak, resolvers=[resolver])

Context: what a subject holds is cached per subject, tenant and permission version: an
assignment bumps the subject's version (`Grants`), an entitlement the tenant's, so
the next call on any replica resolves anew — no wait for a token to expire. Permissions land in
`Identity.permissions`, and `has_permission` stays a set lookup.
"""

from collections.abc import Iterable, Mapping
from datetime import timedelta
from fnmatch import fnmatchcase

from sincpro_framework.auth.common.domain.identity import Identity
from sincpro_framework.auth.common.domain.revocations import Revocations, tenant_subject
from sincpro_framework.auth.grants.domain.assignments import GrantSource, Held
from sincpro_framework.auth.grants.domain.catalog import CatalogSource
from sincpro_framework.data_layer.caching import Cache, CachePolicy, JsonCodec, TimeToLive


class RoleMapping:
    """The roles a token carries, named as the catalog names them.

    RoleMapping({"app:billing": "billing.clerk", "sincpro:finance": ["billing.manager"]})
        .mapped({"app:billing", "offline_access"})   ->  {"billing.clerk"}
    """

    def __init__(
        self, mapping: Mapping[str, str | Iterable[str]], keep_unmapped: bool = False
    ) -> None:
        """`keep_unmapped` keeps a role the mapping does not name, as it came."""
        self.mapping = {
            key: frozenset({value}) if isinstance(value, str) else frozenset(value)
            for key, value in mapping.items()
        }
        self.keep_unmapped = keep_unmapped

    def mapped(self, roles: Iterable[str]) -> frozenset[str]:
        """The catalog's roles for `roles`."""
        found: set[str] = set()
        for role in roles:
            if role in self.mapping:
                found |= self.mapping[role]
            elif self.keep_unmapped:
                found.add(role)
        return frozenset(found)


class ScopeCeiling:
    """What a token's scopes allow, as permission patterns — a client acting for a person never
    does more than its scopes say, whatever the person may.

        ScopeCeiling({"billing:read": ["billing.*.read"], "billing:write": ["billing.*"]},
                     first_party={"billing-web"})

    Context: a first-party client — the service's own application — acts with everything its
    person holds: its token carries `openid profile`, never the service's scopes.
    """

    def __init__(
        self, scopes: Mapping[str, Iterable[str]], first_party: Iterable[str] = ()
    ) -> None:
        self.scopes = {scope: tuple(patterns) for scope, patterns in scopes.items()}
        self.first_party = frozenset(first_party)

    def narrowed(self, permissions: frozenset[str], identity: Identity) -> frozenset[str]:
        """`permissions` a pattern of the identity's scopes allows — all of them for a
        first-party client."""
        if identity.client is not None and identity.client in self.first_party:
            return permissions
        patterns = [one for scope in identity.scopes for one in self.scopes.get(scope, ())]
        return frozenset(
            permission
            for permission in permissions
            if any(fnmatchcase(permission, pattern) for pattern in patterns)
        )


class GrantResolver:
    """An `IdentityResolver` over a catalog — a `GrantCatalog`, or a `LiveCatalog` with the
    roles defined at runtime — and, given one, a `GrantSource`."""

    def __init__(
        self,
        catalog: CatalogSource,
        source: GrantSource | None = None,
        role_mapping: RoleMapping | None = None,
        scope_ceiling: ScopeCeiling | None = None,
        entitlements: bool = False,
        revocations: Revocations | None = None,
        cache: Cache | None = None,
        cache_for: timedelta = timedelta(minutes=5),
    ) -> None:
        """`entitlements` gates permissions by the tenant's — kept by `source`; `revocations`
        keys the cache by the permission versions it keeps."""
        self.catalog = catalog
        self.source = source
        self.role_mapping = role_mapping
        self.scope_ceiling = scope_ceiling
        self.entitlements = entitlements
        self.revocations = revocations
        self.cache = cache or Cache(namespace="auth-grants")
        self._policy: CachePolicy[Held] = CachePolicy(freshness=TimeToLive(ttl=cache_for))
        self._codec = JsonCodec(Held)

    def _versions(self, identity: Identity) -> tuple[int, int]:
        """The subject's and the tenant's permission versions — 0 without revocations; the ones
        the provider already read in this authentication, never read twice."""
        if self.revocations is None:
            return 0, 0
        if not (self.entitlements and identity.tenant):
            return self.revocations.permission_version(identity.subject), 0
        tenant = tenant_subject(identity.tenant)
        versions = self.revocations.permission_versions(identity.subject, tenant)
        return versions[identity.subject], versions[tenant]

    def held(self, identity: Identity) -> Held:
        """What the source says `identity` holds — cached per subject, tenant and versions."""
        source = self.source
        if source is None:
            return Held()
        key = ("held", identity.subject, identity.tenant, *self._versions(identity))
        return self.cache.get_or_compute(
            key,
            lambda: source.held(identity.subject, identity.tenant),
            self._policy,
            self._codec,
        )

    def resolve(self, identity: Identity) -> Identity:
        """`identity` with the roles it holds — their closure — and what they grant.

        1. The token's roles, mapped to the catalog's (as they came without a mapping).
        2. The roles the source assigns that the tenant's catalog declares, unioned — an
           assignment of a role removed, or retired, grants nothing, nor passes `auth.role(...)`.
        3. Their closure, and what it grants — beside what the token itself carried.
        4. Gated by the tenant's entitlements, when asked and the source keeps them.
        5. Narrowed by the scope ceiling, when there is one.
        Final: when 4 or 5 took a permission away, only the roles whose every grant is left —
        `auth.role(...)` never admits a client, or a tenant, the permissions refuse: a reports
        app scoped to `billing:read` acts as a clerk, never as the manager its person is.
        """
        from_token = (
            self.role_mapping.mapped(identity.roles) if self.role_mapping else identity.roles
        )
        held = self.held(identity)
        composition = self.catalog.in_effect(identity.tenant)
        catalog = composition.catalog
        assigned = frozenset(
            one
            for one in held.roles
            if one in catalog.roles and one not in composition.retired
        )
        roles = catalog.closure(from_token | assigned)
        granted = identity.permissions | catalog.permissions_of(roles)
        permissions = granted
        if self.entitlements and held.entitlements is not None:
            permissions = catalog.gated(permissions, held.entitlements)
        if self.scope_ceiling is not None:
            permissions = self.scope_ceiling.narrowed(permissions, identity)
        if permissions != granted:
            roles = frozenset(
                one for one in roles if catalog.permissions_of({one}) <= permissions
            )
        return identity.model_copy(update={"roles": roles, "permissions": permissions})
