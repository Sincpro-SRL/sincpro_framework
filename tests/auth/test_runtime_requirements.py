"""Requirements an administrator adds to a use case at runtime, beside its code's declaration —
for every tenant or for one: judged as `requires` judges on every call, only ever restricting,
named by the Command's dotted name so two contexts never share them, bounded by who may
administer the scope, current on every replica within a second, and published."""

from typing import Any, cast

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    AddedRequirement,
    Credentials,
    Identity,
    InMemoryAddedRequirements,
    InvalidGrant,
    KeyValueAddedRequirements,
    Permission,
    PermissionDenied,
    RequirementAdded,
    RequirementRemoved,
    RuntimeRequirements,
    Unauthenticated,
    security,
)
from sincpro_framework.auth.access_control.domain.added_requirements import AddedRequirements
from sincpro_framework.auth.server import StaticProvider
from sincpro_framework.data_layer.store import InMemoryKeyValue, StoreUnavailable
from sincpro_framework.exceptions import ProgrammingError


class P(Permission):
    READ = "billing.invoice.read"
    APPROVE = "billing.payment.approve"


class CommandApprovePayment(DataTransferObject):
    number: str


class CommandReadInvoice(DataTransferObject):
    number: str


class Answer(DataTransferObject):
    by: str = ""


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Recorder:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, event: Any, /) -> None:
        self.events.append(event)


APPROVE = f"{__name__}.CommandApprovePayment"


def billing(auth: AccessControl[P]) -> UseFramework:
    """A bus whose approval requires reading, in code — and nothing more."""
    bus = UseFramework("billing", log_after_execution=False)

    @bus.feature(CommandApprovePayment)
    @auth.requires(P.READ)
    class Approve(Feature):
        def execute(self, dto: CommandApprovePayment) -> Answer:
            return Answer(by=security.identity.subject)

    @bus.feature(CommandReadInvoice)
    class ReadInvoice(Feature):
        def execute(self, dto: CommandReadInvoice) -> Answer:
            return Answer(by=security.identity.subject)

    auth.on(bus)
    return bus


ANA = Identity.user("user:ana", tenant="bo", permissions={P.READ})
BEA = Identity.user("user:bea", tenant="pe", permissions={P.READ})
CARLA = Identity.user(
    "user:carla", tenant="bo", roles={"cash.supervisor@bo"}, permissions={P.READ, P.APPROVE}
)


def guarded(store: Any = None, **options: Any) -> AccessControl[P]:
    """An `AccessControl` that requires at runtime — on `store`, in memory when not given."""
    return AccessControl[P](runtime=store or InMemoryAddedRequirements(), **options)


def run(bus: UseFramework, dto: Any, identity: Identity) -> Answer:
    with security.as_identity(identity):
        answered = bus(dto, Answer)
    assert answered is not None
    return answered


def test_a_requirement_added_for_every_tenant_holds_on_every_call() -> None:
    audit = Recorder()
    auth = guarded(publisher=audit)
    bus = billing(auth)
    assert run(bus, CommandApprovePayment(number="F-1"), ANA).by == "user:ana"
    with security.as_system("the platform"):
        auth.runtime.require(CommandApprovePayment, P.APPROVE)
    with pytest.raises(PermissionDenied, match="billing.payment.approve"):
        run(bus, CommandApprovePayment(number="F-1"), ANA)
    assert run(bus, CommandApprovePayment(number="F-1"), CARLA).by == "user:carla"
    assert (
        run(bus, CommandReadInvoice(number="F-1"), ANA).by == "user:ana"
    )  # others untouched
    with pytest.raises(Unauthenticated):
        with security.as_identity(Identity.anonymous()):
            bus(CommandApprovePayment(number="F-1"), Answer)
    with security.as_system("the platform"):
        auth.runtime.release(CommandApprovePayment)
    assert run(bus, CommandApprovePayment(number="F-1"), ANA).by == "user:ana"
    added, removed = [
        one for one in audit.events if isinstance(one, (RequirementAdded, RequirementRemoved))
    ]  # beside the refusals
    assert isinstance(added, RequirementAdded) and isinstance(removed, RequirementRemoved)
    assert (added.entity_type, added.entity_id, added.permissions, added.tenant) == (
        "auth.use_case",
        APPROVE,
        ["billing.payment.approve"],
        None,
    )


def test_a_tenants_requirement_holds_for_that_tenant_alone_and_only_adds() -> None:
    auth = guarded()
    bus = billing(auth)
    bo_admin = Identity.user(
        "user:root-bo",
        tenant="bo",
        roles={"cash.supervisor@bo"},
        permissions={P.READ, P.APPROVE},
    )
    with security.as_identity(bo_admin):
        auth.runtime.require(APPROVE, auth.role("cash.supervisor@bo"), tenant="bo")
    with pytest.raises(PermissionDenied, match="holds no cash.supervisor@bo"):
        run(bus, CommandApprovePayment(number="F-1"), ANA)
    assert run(bus, CommandApprovePayment(number="F-1"), CARLA).by == "user:carla"
    assert run(bus, CommandApprovePayment(number="F-1"), BEA).by == "user:bea"  # tenant pe
    nobody_reads = Identity.user("user:dan", tenant="bo", roles={"cash.supervisor@bo"})
    with pytest.raises(
        PermissionDenied, match="billing.invoice.read"
    ):  # the code still holds
        run(bus, CommandApprovePayment(number="F-1"), nobody_reads)


def test_changing_them_is_bounded_by_who_administers() -> None:
    auth = guarded()
    billing(auth)
    bo_admin = Identity.user("user:root-bo", tenant="bo")
    with security.as_identity(bo_admin):
        with pytest.raises(PermissionDenied, match="every tenant"):
            auth.runtime.require(APPROVE, P.APPROVE)
        with pytest.raises(PermissionDenied, match="in tenant pe, not bo"):
            auth.runtime.require(APPROVE, P.APPROVE, tenant="pe")
    with pytest.raises(Unauthenticated):
        auth.runtime.require(APPROVE, P.APPROVE, tenant="bo")


def test_what_cannot_hold_is_refused() -> None:
    auth = guarded()
    billing(auth)
    with security.as_system("the platform"):
        with pytest.raises(InvalidGrant, match="is not a use case"):
            auth.runtime.require("billing.CommandGhost", P.APPROVE)
        with pytest.raises(InvalidGrant, match="no code declares"):
            auth.runtime.require(APPROVE, "billing.everything")
        with pytest.raises(InvalidGrant, match="name a permission or a role"):
            auth.runtime.require(APPROVE)
        with pytest.raises(InvalidGrant, match="in one auth.role"):
            auth.runtime.require(APPROVE, auth.role("a"), auth.role("b"))
        with pytest.raises(InvalidGrant, match="only permissions and auth.role"):
            auth.runtime.require(APPROVE, cast(Any, auth.rule("mine", lambda call: True)))
    with pytest.raises(ProgrammingError, match="runtime=True"):
        AccessControl[P]().runtime.require(APPROVE, P.APPROVE)


def test_two_contexts_with_a_command_of_one_name_never_share_what_is_added() -> None:
    auth = guarded()
    billing(auth)
    payroll = UseFramework("payroll", log_after_execution=False)

    class CommandApprovePayment(DataTransferObject):  # payroll's own, of the same name
        number: str

    @payroll.feature(CommandApprovePayment)
    class ApproveSalary(Feature):
        def execute(self, dto: CommandApprovePayment) -> Answer:
            return Answer(by=security.identity.subject)

    auth.on(payroll)
    with security.as_system("the platform"):
        auth.runtime.require(APPROVE, P.APPROVE)  # billing's
    assert run(payroll, CommandApprovePayment(number="S-1"), ANA).by == "user:ana"


def test_another_replica_holds_it_once_it_checks_again() -> None:
    store, clock = InMemoryKeyValue(), Clock()
    here, there = guarded(KeyValueAddedRequirements(store)), AccessControl[P]()
    billing(here)
    there_bus = billing(there)
    reader = RuntimeRequirements(there, KeyValueAddedRequirements(store), clock=clock)
    there.every_call(reader.check, name="requirements added at runtime")
    assert run(there_bus, CommandApprovePayment(number="F-1"), ANA).by == "user:ana"
    with security.as_system("the platform"):
        here.runtime.require(APPROVE, P.APPROVE)
    assert run(there_bus, CommandApprovePayment(number="F-1"), ANA).by == "user:ana"
    clock.now += 1.0
    with pytest.raises(PermissionDenied):
        run(there_bus, CommandApprovePayment(number="F-1"), ANA)


def test_a_catalog_lists_what_may_be_required_and_describe_names_the_check() -> None:
    auth = guarded()
    billing(auth)
    assert APPROVE in auth.guarded_use_cases()
    assert auth.describe().every_call == ["requirements added at runtime"]


def test_a_weaker_administrator_never_shuts_out_a_stronger_one() -> None:
    auth = guarded()
    billing(auth)
    with security.as_identity(CARLA):  # bo: holds the supervisor role and approves
        auth.runtime.require(APPROVE, auth.role("cash.supervisor@bo"), tenant="bo")
    reader_admin = Identity.user("user:reader", tenant="bo", permissions={P.READ})
    with security.as_identity(reader_admin):
        with pytest.raises(PermissionDenied, match="needs billing.payment.approve"):
            auth.runtime.require(CommandReadInvoice, P.APPROVE, tenant="bo")
        with pytest.raises(PermissionDenied, match="needs cash.supervisor@bo"):
            auth.runtime.require(APPROVE, P.READ, tenant="bo")  # what it replaces
        with pytest.raises(PermissionDenied, match="needs cash.supervisor@bo"):
            auth.runtime.release(APPROVE, tenant="bo")


class DownFromTheStart(InMemoryAddedRequirements):
    def read(self, scope: str | None = None) -> Any:
        raise StoreUnavailable("redis is down")


def test_a_tenant_never_read_while_the_store_is_down_is_refused_never_let_through() -> None:
    auth = guarded(DownFromTheStart())
    bus = billing(auth)
    with pytest.raises(StoreUnavailable):  # 503: what only restricts fails closed
        run(bus, CommandApprovePayment(number="F-1"), CARLA)


def test_a_permission_this_replica_does_not_know_is_never_met_never_a_crash() -> None:
    store = InMemoryAddedRequirements()
    store.write(
        None,
        AddedRequirements(
            version=1,
            requirements=(
                AddedRequirement(command=APPROVE, permissions=frozenset({"billing.v2"})),
            ),
        ),
    )
    bus = billing(guarded(store))
    with pytest.raises(
        PermissionDenied, match="billing.v2, which this service does not know"
    ):
        run(bus, CommandApprovePayment(number="F-1"), CARLA)


def test_the_platform_releases_what_shut_an_administrator_out() -> None:
    auth = guarded()
    bus = billing(auth)
    with security.as_identity(CARLA):
        auth.runtime.require(APPROVE, auth.role("cash.supervisor@bo"), tenant="bo")
    other_admin = Identity.user("user:root-bo", tenant="bo", permissions={P.READ, P.APPROVE})
    with pytest.raises(PermissionDenied, match="holds no cash.supervisor@bo"):
        run(bus, CommandApprovePayment(number="F-1"), other_admin)
    with security.as_system("support"):
        auth.runtime.release(APPROVE, tenant="bo")
    assert run(bus, CommandApprovePayment(number="F-1"), other_admin).by == "user:root-bo"


def test_a_copy_for_a_test_judges_what_is_added_with_its_own_providers() -> None:
    auth = guarded()
    bus = billing(auth)
    with security.as_system("the platform"):
        auth.runtime.require(APPROVE, P.APPROVE)
    test = bus.fresh()
    tester = Identity.user("user:tester", permissions={P.READ, P.APPROVE})
    test_auth = auth.using(providers=[StaticProvider({"t": tester})])
    test_auth.on(test, replaces=auth)
    credentials = Credentials(transport="http", headers={"authorization": "Bearer t"})
    with security.authenticated_as(test, credentials):
        assert test(CommandApprovePayment(number="F-1"), Answer).by == "user:tester"
    assert test_auth.runtime is not auth.runtime
    assert test_auth.runtime.store is auth.runtime.store
