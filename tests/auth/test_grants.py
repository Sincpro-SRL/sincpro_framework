"""Grants the Odoo 20 way: a catalog of roles grouped in categories and privileges, each level
implying the one below, disjoint roles never held together, entitlements a tenant bought; stores
that keep only what was assigned; a resolver that turns token roles and assignments into
permissions, narrowed by scopes — and a role taken away holding on the very next call."""

from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    Credentials,
    Identity,
    InMemoryRevocations,
    InvalidGrant,
    KeyValueRevocations,
    Permission,
    PermissionDenied,
    security,
)
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.grants import (
    Assignment,
    Category,
    Entitlement,
    GrantAdministration,
    GrantCatalog,
    GrantResolver,
    GrantStore,
    InMemoryGrants,
    KeyValueGrants,
    Privilege,
    Role,
    RoleMapping,
    RolePermissions,
    ScopeCeiling,
)
from sincpro_framework.auth.infrastructure import jws
from sincpro_framework.auth.server import (
    ClaimMapping,
    JwtProvider,
    SharedSecrets,
    TrustedIssuer,
)
from sincpro_framework.data_layer.caching import Cache
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.data_layer.store.adapters.redis import RedisKeyValue
from sincpro_framework.runtime.testing import GrantStoreContract, ManualClock


class P(Permission):
    READ = "billing.invoice.read"
    ISSUE = "billing.invoice.issue"
    APPROVE = "billing.payment.approve"
    PORTAL = "portal.invoice.read"


accounting = Category("accounting", "Accounting", sequence=1)
invoicing = Privilege("invoicing", "Invoicing", category=accounting, placeholder="No access")
clerk = Role("billing.clerk", "Clerk", grants={P.READ}, privilege=invoicing, sequence=10)
manager = Role(
    "billing.manager",
    "Manager",
    grants={P.ISSUE},
    implies=(clerk,),
    privilege=invoicing,
    sequence=20,
)
approver = Role("billing.approver", "Approver", grants={P.APPROVE}, implies=(manager,))
portal = Role("portal", "Portal", grants={P.PORTAL}, disjoint={"billing.clerk"})
catalog = GrantCatalog(
    [approver, portal], entitlements=[Entitlement("e-invoicing", gates={P.ISSUE})]
)


def test_a_role_grants_what_it_implies_transitively() -> None:
    assert catalog.closure({"billing.approver"}) == {
        "billing.approver",
        "billing.manager",
        "billing.clerk",
    }
    assert catalog.permissions_of({"billing.approver"}) == {P.READ, P.ISSUE, P.APPROVE}
    assert catalog.permissions_of({"offline_access"}) == frozenset()
    assert catalog.closure({"offline_access"}) == {"offline_access"}


def test_disjoint_roles_and_two_levels_of_one_privilege_conflict() -> None:
    assert catalog.conflicts({"portal", "billing.manager"}) == [
        "billing.clerk and portal are never held together"
    ]
    assert catalog.conflicts({"billing.clerk", "billing.manager"}) == [
        "invoicing is given at several levels: billing.clerk, billing.manager"
    ]
    assert catalog.conflicts({"billing.approver"}) == []
    assert catalog.level_of("invoicing", {"billing.approver"}) == manager
    assert catalog.level_of("invoicing", {"portal"}) is None


def test_a_catalog_that_cannot_hold_is_a_programming_error() -> None:
    loose = Role("billing.manager", grants={P.ISSUE}, privilege=invoicing, sequence=20)
    with pytest.raises(exceptions.ProgrammingError, match="does not imply"):
        GrantCatalog([clerk, loose])
    with pytest.raises(exceptions.ProgrammingError, match="two roles"):
        GrantCatalog([clerk, Role("billing.clerk", grants={P.ISSUE})])
    with pytest.raises(exceptions.ProgrammingError, match="nobody declared"):
        GrantCatalog([Role("a", disjoint={"ghost"})])


def test_the_catalog_describes_itself_for_an_administration_screen() -> None:
    described = catalog.describe()
    assert [(one.key, one.privileges) for one in described.categories] == [
        ("accounting", ["invoicing"])
    ]
    assert described.privileges["invoicing"].levels == ["billing.clerk", "billing.manager"]
    assert described.privileges["invoicing"].placeholder == "No access"
    assert described.roles["billing.clerk"].all_implied_by == [
        "billing.approver",
        "billing.manager",
    ]
    assert described.roles["billing.approver"].all_implied == [
        "billing.clerk",
        "billing.manager",
    ]
    assert described.other_roles == ["billing.approver", "portal"]
    assert described.entitlements == {"e-invoicing": [P.ISSUE]}


def test_a_role_permissions_table_becomes_a_catalog() -> None:
    table = RolePermissions(
        {"accountant": {P.ISSUE}, "viewer": {P.READ}},
        implies={"accountant": {"viewer"}, "viewer": {"accountant"}},
    )
    built = GrantCatalog.from_role_permissions(table)
    assert built.permissions_of({"accountant"}) == table.permissions_of({"accountant"})


class TestInMemoryGrants(GrantStoreContract):
    def make_store(self) -> GrantStore:
        return InMemoryGrants()


class TestKeyValueGrants(GrantStoreContract):
    def make_store(self) -> GrantStore:
        return KeyValueGrants(InMemoryKeyValue())


class TestGrantsOnRedis(GrantStoreContract):
    def make_store(self) -> GrantStore:
        return KeyValueGrants(RedisKeyValue(fakeredis.FakeRedis()))


def test_token_roles_and_assigned_roles_both_grant() -> None:
    grants = InMemoryGrants()
    grants.assign(Assignment(subject="user:ana", tenant="bo", role="billing.approver"))
    resolver = GrantResolver(
        catalog, grants, role_mapping=RoleMapping({"app:billing": "billing.clerk"})
    )
    from_token = resolver.resolve(
        Identity.user("user:bea", tenant="bo", roles={"app:billing"})
    )
    assert from_token.permissions == {P.READ} and from_token.roles == {"billing.clerk"}
    assigned = resolver.resolve(Identity.user("user:ana", tenant="bo"))
    assert assigned.permissions == {P.READ, P.ISSUE, P.APPROVE}


def test_scopes_cap_a_third_party_client_never_the_services_own() -> None:
    ceiling = ScopeCeiling({"billing:read": ["billing.*.read"]}, first_party={"billing-web"})
    resolver = GrantResolver(catalog, scope_ceiling=ceiling)
    held = Identity.user("user:ana", roles={"billing.approver"}, scopes={"billing:read"})
    third = resolver.resolve(held.model_copy(update={"client": "reports-app"}))
    own = resolver.resolve(held.model_copy(update={"client": "billing-web"}))
    assert third.permissions == {P.READ}
    assert own.permissions == {P.READ, P.ISSUE, P.APPROVE}
    assert third.roles == {"billing.clerk"}  # a role is held only while all it grants is
    assert own.roles == {"billing.approver", "billing.manager", "billing.clerk"}


def test_a_tenant_without_the_entitlement_is_never_granted_what_it_gates() -> None:
    grants = InMemoryGrants()
    for tenant in ("bo", "pe"):
        grants.assign(Assignment(subject="user:ana", tenant=tenant, role="billing.manager"))
    grants.entitle("bo", "e-invoicing")
    resolver = GrantResolver(catalog, grants, entitlements=True)
    assert P.ISSUE in resolver.resolve(Identity.user("user:ana", tenant="bo")).permissions
    not_entitled = resolver.resolve(Identity.user("user:ana", tenant="pe"))
    assert not_entitled.permissions == {P.READ}
    assert not_entitled.roles == {"billing.clerk"}


def test_administration_refuses_what_the_catalog_does_not_allow() -> None:
    grants = InMemoryGrants()
    admin = GrantAdministration(catalog, grants)
    with pytest.raises(InvalidGrant, match="is not a role"):
        admin.assign("user:ana", "bo", "billing.ghost")
    admin.assign("user:ana", "bo", "portal")
    with pytest.raises(InvalidGrant, match="never held together"):
        admin.assign("user:ana", "bo", "billing.manager")
    with pytest.raises(InvalidGrant, match="not an entitlement"):
        admin.entitle("bo", "payroll")
    assert grants.held("user:ana", "bo").roles == {"portal"}


def test_one_level_per_privilege() -> None:
    grants = InMemoryGrants()
    admin = GrantAdministration(catalog, grants)
    admin.set_level("user:ana", "bo", "invoicing", "billing.clerk")
    admin.set_level("user:ana", "bo", "invoicing", "billing.manager")
    assert grants.held("user:ana", "bo").roles == {"billing.manager"}
    admin.set_level("user:ana", "bo", "invoicing", None)
    assert grants.held("user:ana", "bo").roles == frozenset()
    with pytest.raises(InvalidGrant, match="not a level"):
        admin.set_level("user:ana", "bo", "invoicing", "portal")


def test_without_revocations_an_assignment_holds_once_the_cache_lapses() -> None:
    clock = ManualClock(datetime(2026, 11, 20, tzinfo=UTC))
    grants = InMemoryGrants()
    resolver = GrantResolver(
        catalog, grants, cache=Cache(now=clock.now), cache_for=timedelta(minutes=5)
    )
    ana = Identity.user("user:ana", tenant="bo")
    assert resolver.resolve(ana).permissions == frozenset()
    grants.assign(Assignment(subject="user:ana", tenant="bo", role="billing.clerk"))
    assert resolver.resolve(ana).permissions == frozenset()
    clock.advance(minutes=5)
    assert resolver.resolve(ana).permissions == {P.READ}


ISSUER = "https://auth.acme.bo/realms/acme"
SECRET = "a shared secret of thirty-two by!"


class CommandIssue(DataTransferObject):
    total: int = 1


class Issued(DataTransferObject):
    by: str


def test_a_role_taken_away_holds_on_the_very_next_call() -> None:
    grants, revocations = InMemoryGrants(), InMemoryRevocations()
    admin = GrantAdministration(catalog, grants, revocations)
    admin.set_level("user:ana", "bo", "invoicing", "billing.manager")
    provider = JwtProvider(
        TrustedIssuer(
            ISSUER,
            audience="billing",
            keys=SharedSecrets({"k": SECRET}),
            mapping=ClaimMapping(tenant="tenant"),
        ),
        resolvers=[GrantResolver(catalog, grants, revocations=revocations)],
    )
    auth = AccessControl[P](providers=[provider])
    billing = UseFramework("grants-billing", log_after_execution=False)

    @billing.feature(CommandIssue)
    @auth.requires(P.ISSUE)
    class Issue(Feature):
        def execute(self, dto: CommandIssue) -> Issued:
            return Issued(by=security.identity.subject)

    auth.on(billing)
    token = jws.encode(
        {"alg": "HS256", "kid": "k"},
        {
            "iss": ISSUER,
            "aud": "billing",
            "sub": "ana",
            "tenant": "bo",
            "exp": datetime.now(UTC).timestamp() + 600,
        },
        jws.HmacKey.of("k", SECRET).sign,
    )
    credentials = Credentials(transport="http", headers={"authorization": f"Bearer {token}"})

    def call() -> Issued | None:
        with security.authenticated_as(billing, credentials):
            return billing(CommandIssue(), Issued)

    first = call()
    assert first is not None and first.by == "user:ana"
    admin.set_level("user:ana", "bo", "invoicing", "billing.clerk")
    with pytest.raises(PermissionDenied):
        call()


class CountedStore(InMemoryKeyValue):
    """An in-memory store that counts its reads."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        self.reads += 1
        return super().get_many(keys)


def test_the_revocation_check_and_the_resolver_share_one_read() -> None:
    store, grants = CountedStore(), InMemoryGrants()
    revocations = KeyValueRevocations(store)
    admin = GrantAdministration(catalog, grants, revocations)
    admin.set_level("user:ana", "bo", "invoicing", "billing.manager")
    admin.entitle("bo", "e-invoicing")
    provider = JwtProvider(
        TrustedIssuer(
            ISSUER,
            audience="billing",
            keys=SharedSecrets({"k": SECRET}),
            mapping=ClaimMapping(tenant="tenant"),
        ),
        revocations=revocations,
        resolvers=[
            GrantResolver(catalog, grants, entitlements=True, revocations=revocations)
        ],
    )
    token = jws.encode(
        {"alg": "HS256", "kid": "k"},
        {
            "iss": ISSUER,
            "aud": "billing",
            "sub": "ana",
            "tenant": "bo",
            "jti": "t-1",
            "exp": datetime.now(UTC).timestamp() + 600,
        },
        jws.HmacKey.of("k", SECRET).sign,
    )
    credentials = Credentials(transport="http", headers={"authorization": f"Bearer {token}"})
    store.reads = 0
    identity = provider.authenticate(credentials)
    assert identity is not None and P.ISSUE in identity.permissions
    assert store.reads == 1
    admin.withdraw("bo", "e-invoicing")
    store.reads = 0
    identity = provider.authenticate(credentials)
    assert identity is not None and P.ISSUE not in identity.permissions
    assert store.reads == 1
