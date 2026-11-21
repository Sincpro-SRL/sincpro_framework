"""Requirements an administrator adds to a use case at runtime, beside its code's declaration —
for every tenant or for one: judged as `requires` judges on every call, only ever restricting,
named by the Command's dotted name so two contexts never share them, bounded by who may
administer the scope, current on every replica within a second, and published."""

from typing import Any

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
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
from sincpro_framework.data_layer.store import InMemoryKeyValue


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
    "user:carla", tenant="bo", roles={"cash.supervisor"}, permissions={P.READ, P.APPROVE}
)


def run(bus: UseFramework, dto: Any, identity: Identity) -> Answer:
    with security.as_identity(identity):
        answered = bus(dto, Answer)
    assert answered is not None
    return answered


def test_a_requirement_added_for_every_tenant_holds_on_every_call() -> None:
    auth, audit = AccessControl[P](), Recorder()
    bus = billing(auth)
    runtime = auth.require_at_runtime(InMemoryAddedRequirements(), P, publisher=audit)
    assert run(bus, CommandApprovePayment(number="F-1"), ANA).by == "user:ana"
    with security.as_system("the platform"):
        runtime.require(CommandApprovePayment, permissions={P.APPROVE})
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
        runtime.release(CommandApprovePayment)
    assert run(bus, CommandApprovePayment(number="F-1"), ANA).by == "user:ana"
    added, removed = audit.events
    assert isinstance(added, RequirementAdded) and isinstance(removed, RequirementRemoved)
    assert (added.entity_type, added.entity_id, added.permissions, added.tenant) == (
        "auth.use_case",
        APPROVE,
        ["billing.payment.approve"],
        None,
    )


def test_a_tenants_requirement_holds_for_that_tenant_alone_and_only_adds() -> None:
    auth = AccessControl[P]()
    bus = billing(auth)
    runtime = auth.require_at_runtime(InMemoryAddedRequirements(), P)
    bo_admin = Identity.user(
        "user:root-bo",
        tenant="bo",
        roles={"cash.supervisor"},
        permissions={P.READ, P.APPROVE},
    )
    with security.as_identity(bo_admin):
        runtime.require(APPROVE, roles={"cash.supervisor"}, tenant="bo")
    with pytest.raises(PermissionDenied, match="holds no cash.supervisor"):
        run(bus, CommandApprovePayment(number="F-1"), ANA)
    assert run(bus, CommandApprovePayment(number="F-1"), CARLA).by == "user:carla"
    assert run(bus, CommandApprovePayment(number="F-1"), BEA).by == "user:bea"  # tenant pe
    nobody_reads = Identity.user("user:dan", tenant="bo", roles={"cash.supervisor"})
    with pytest.raises(
        PermissionDenied, match="billing.invoice.read"
    ):  # the code still holds
        run(bus, CommandApprovePayment(number="F-1"), nobody_reads)


def test_changing_them_is_bounded_by_who_administers() -> None:
    auth = AccessControl[P]()
    billing(auth)
    runtime = auth.require_at_runtime(InMemoryAddedRequirements(), P)
    bo_admin = Identity.user("user:root-bo", tenant="bo")
    with security.as_identity(bo_admin):
        with pytest.raises(PermissionDenied, match="every tenant"):
            runtime.require(APPROVE, permissions={P.APPROVE})
        with pytest.raises(PermissionDenied, match="in tenant pe, not bo"):
            runtime.require(APPROVE, permissions={P.APPROVE}, tenant="pe")
    with pytest.raises(Unauthenticated):
        runtime.require(APPROVE, permissions={P.APPROVE}, tenant="bo")


def test_what_cannot_hold_is_refused() -> None:
    auth = AccessControl[P]()
    billing(auth)
    runtime = auth.require_at_runtime(InMemoryAddedRequirements(), P)
    with security.as_system("the platform"):
        with pytest.raises(InvalidGrant, match="is not a use case"):
            runtime.require("billing.CommandGhost", permissions={P.APPROVE})
        with pytest.raises(InvalidGrant, match="no code declares"):
            runtime.require(APPROVE, permissions={"billing.everything"})
        with pytest.raises(InvalidGrant, match="name a permission or a role"):
            runtime.require(APPROVE)
    with pytest.raises(Exception, match="already requires at runtime"):
        auth.require_at_runtime(InMemoryAddedRequirements(), P)


def test_two_contexts_with_a_command_of_one_name_never_share_what_is_added() -> None:
    auth = AccessControl[P]()
    billing(auth)
    payroll = UseFramework("payroll", log_after_execution=False)

    class CommandApprovePayment(DataTransferObject):  # payroll's own, of the same name
        number: str

    @payroll.feature(CommandApprovePayment)
    class ApproveSalary(Feature):
        def execute(self, dto: CommandApprovePayment) -> Answer:
            return Answer(by=security.identity.subject)

    auth.on(payroll)
    runtime = auth.require_at_runtime(InMemoryAddedRequirements(), P)
    with security.as_system("the platform"):
        runtime.require(APPROVE, permissions={P.APPROVE})  # billing's
    assert run(payroll, CommandApprovePayment(number="S-1"), ANA).by == "user:ana"


def test_another_replica_holds_it_once_it_checks_again() -> None:
    store, clock = InMemoryKeyValue(), Clock()
    here, there = AccessControl[P](), AccessControl[P]()
    billing(here)
    there_bus = billing(there)
    writer = here.require_at_runtime(KeyValueAddedRequirements(store), P)
    reader = RuntimeRequirements(there, KeyValueAddedRequirements(store), P, clock=clock)
    there.every_call(reader.check, name="requirements added at runtime")
    assert run(there_bus, CommandApprovePayment(number="F-1"), ANA).by == "user:ana"
    with security.as_system("the platform"):
        writer.require(APPROVE, permissions={P.APPROVE})
    assert run(there_bus, CommandApprovePayment(number="F-1"), ANA).by == "user:ana"
    clock.now += 1.0
    with pytest.raises(PermissionDenied):
        run(there_bus, CommandApprovePayment(number="F-1"), ANA)


def test_a_catalog_lists_what_may_be_required_and_describe_names_the_check() -> None:
    auth = AccessControl[P]()
    billing(auth)
    auth.require_at_runtime(InMemoryAddedRequirements(), P)
    assert APPROVE in auth.guarded_use_cases()
    assert auth.describe().every_call == ["requirements added at runtime"]


def test_a_weaker_administrator_never_shuts_out_a_stronger_one() -> None:
    auth = AccessControl[P]()
    billing(auth)
    runtime = auth.require_at_runtime(InMemoryAddedRequirements(), P)
    with security.as_identity(CARLA):  # bo: holds the supervisor role and approves
        runtime.require(APPROVE, roles={"cash.supervisor"}, tenant="bo")
    reader_admin = Identity.user("user:reader", tenant="bo", permissions={P.READ})
    with security.as_identity(reader_admin):
        with pytest.raises(PermissionDenied, match="needs billing.payment.approve"):
            runtime.require(CommandReadInvoice, permissions={P.APPROVE}, tenant="bo")
        with pytest.raises(PermissionDenied, match="needs cash.supervisor"):
            runtime.require(APPROVE, permissions={P.READ}, tenant="bo")  # what it replaces
        with pytest.raises(PermissionDenied, match="needs cash.supervisor"):
            runtime.release(APPROVE, tenant="bo")
