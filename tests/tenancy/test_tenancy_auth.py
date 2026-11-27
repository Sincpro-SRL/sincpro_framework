"""Tenancy beside auth: one rule for the tenant an identity acts in — its own, else the one the
call names (`acting_tenant`) — read alike by the requirements added at runtime, the roles
assigned per tenant and the entitlements; and tenancy validating, inside the guard, that the
verified subject may name that tenant.

Each test below was a gap the audit of 2026-11 found and confirmed by running it.
"""

from typing import Any

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    Identity,
    InMemoryAddedRequirements,
    PermissionDenied,
    Unauthenticated,
    security,
)
from sincpro_framework.auth.common.infrastructure.claim_mapping import ClaimMapping
from sincpro_framework.auth.grants import Assignment, GrantResolver, InMemoryGrants
from sincpro_framework.tenancy import InMemoryMemberships, Tenancy, TenantNotAllowed
from tests.auth.test_grants import P as GrantP
from tests.auth.test_grants import catalog
from tests.auth.test_runtime_requirements import (
    APPROVE,
    Answer,
    CommandApprovePayment,
    P,
    billing,
)


def _run(bus: UseFramework, dto: Any, identity: Identity, context: dict) -> Answer:
    with security.as_identity(identity), bus.context(context):
        answered = bus(dto, Answer)
    assert answered is not None
    return answered


def test_a_token_of_no_tenant_meets_the_requirements_of_the_tenant_it_acts_in():
    auth = AccessControl[P](runtime=InMemoryAddedRequirements())
    bus = billing(auth)
    with security.as_identity(
        Identity.user("user:root-bo", tenant="bo", roles={"cash.supervisor@bo"})
    ):
        auth.runtime.require(APPROVE, auth.role("cash.supervisor@bo"), tenant="bo")
    of_several = Identity.user("user:multi", permissions={P.READ})

    with pytest.raises(PermissionDenied, match="cash.supervisor@bo"):
        _run(bus, CommandApprovePayment(number="F-1"), of_several, {"tenant_id": "bo"})
    assert _run(bus, CommandApprovePayment(number="F-1"), of_several, {"tenant_id": "pe"}).by


def test_the_roles_assigned_in_a_tenant_hold_where_the_call_acts():
    grants = InMemoryGrants()
    grants.assign(Assignment(subject="user:ana", tenant="bo", role="billing.manager"))
    resolver = GrantResolver(catalog, grants)
    of_several = Identity.user("user:ana")
    with UseFramework("acting", log_after_execution=False).context({"tenant_id": "bo"}):
        assert GrantP.ISSUE in resolver.resolve(of_several).permissions
    assert resolver.resolve(of_several).permissions == frozenset()


def test_a_platform_identity_keeps_what_entitlements_gate():
    grants = InMemoryGrants()
    grants.assign(Assignment(subject="user:root", tenant=None, role="billing.manager"))
    resolver = GrantResolver(catalog, grants, entitlements=True)
    assert GrantP.ISSUE in resolver.resolve(Identity.user("user:root")).permissions


def test_a_tenant_claim_that_is_a_list_is_refused_never_read_as_text():
    mapping = ClaimMapping(tenant="orgs")
    with pytest.raises(Unauthenticated, match="as list"):
        mapping.identity({"sub": "ana", "orgs": ["bo", "pe"]}, "idp")
    assert mapping.identity({"sub": "ana", "orgs": "bo"}, "idp").tenant == "bo"


class CommandWhere(DataTransferObject):
    pass


def test_tenancy_validates_the_verified_subject_inside_the_guard():
    """auth binds who the caller is (`user_id`); tenancy, just inside the guard, checks the
    tenant the caller names against that subject's memberships."""
    auth = AccessControl[P]()
    tenancy = Tenancy(memberships=InMemoryMemberships({"user:ana": ["bo", "pe"]}))
    bus = UseFramework("tenancy-guarded", log_after_execution=False)

    @bus.feature(CommandWhere)
    class Where(Feature):
        def execute(self, dto: CommandWhere) -> Answer:
            return Answer(by=self.tenancy.active or "")

    auth.on(bus)
    tenancy.on(bus)
    ana = Identity.user("user:ana")
    assert _run(bus, CommandWhere(), ana, {"tenant_id": "pe"}).by == "pe"
    with pytest.raises(TenantNotAllowed):
        _run(bus, CommandWhere(), ana, {"tenant_id": "cl"})
    with pytest.raises(TenantNotAllowed):  # a verified subject of no membership there
        _run(bus, CommandWhere(), Identity.user("user:eve"), {"tenant_id": "bo"})


def test_a_platform_role_holds_in_every_tenant_the_platform_enters():
    """root holds billing.manager in no tenant — the platform's —, and keeps it when the call
    names a tenant, beside what it holds there; a tenant's entitlements never take it away."""
    grants = InMemoryGrants()
    grants.assign(Assignment(subject="user:root", tenant=None, role="billing.manager"))
    resolver = GrantResolver(catalog, grants, entitlements=True)
    with UseFramework("acting-platform", log_after_execution=False).context(
        {"tenant_id": "pe"}
    ):
        assert GrantP.ISSUE in resolver.resolve(Identity.user("user:root")).permissions
