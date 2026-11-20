"""Open unless it says otherwise, and said in the project's own terms: a use case that declares
nothing runs for anyone; `requires` takes a permission, a role (Odoo's groups, read off their
closure) or a rule of the project's own, asked with the call and the execution's context;
`every_call` is an interceptor of the project's, asked on every call, open or declared; and a
handler asks the same things of `self.auth`."""

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    OPEN,
    AccessCall,
    AccessControl,
    Category,
    GrantCatalog,
    Identity,
    Permission,
    PermissionDenied,
    Privilege,
    Role,
    Unauthenticated,
    security,
)


class P(Permission):
    READ = "billing.invoice.read"
    APPROVE = "billing.invoice.approve"


class QueryHealth(DataTransferObject):
    pass


class QueryInvoice(DataTransferObject):
    number: str
    owner: str


class CommandApprove(DataTransferObject):
    number: str


class CommandReview(DataTransferObject):
    number: str


class Answer(DataTransferObject):
    by: str = ""
    may_approve: bool = False


ANA = Identity.user("user:ana", tenant="bo", roles={"billing.manager"}, permissions={P.READ})
BEA = Identity.user("user:bea", tenant="pe", roles={"billing.clerk"}, permissions={P.READ})
SUSPENDED = {"pe"}


def billing() -> tuple[UseFramework, AccessControl[P]]:
    """A billing context: health open, an invoice read by its owner, an approval for managers,
    a review asking `self.auth` by hand — and an interceptor refusing suspended tenants."""
    auth = AccessControl[P]()
    bus = UseFramework("open-billing", log_after_execution=False)
    own_invoice = auth.rule(
        "its own invoice", lambda call: call.dto.owner == call.identity.subject
    )

    @auth.every_call
    def tenant_active(call: AccessCall) -> bool:
        return call.context.get("tenant_id") not in SUSPENDED

    @bus.feature(QueryHealth)
    class Health(Feature):
        def execute(self, dto: QueryHealth) -> Answer:
            return Answer(by=security.identity.subject)

    @bus.feature(QueryInvoice)
    @auth.requires(P.READ, own_invoice)
    class ReadInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> Answer:
            return Answer(by=security.identity.subject)

    @bus.feature(CommandApprove)
    @auth.requires(auth.role("billing.manager", "billing.admin"))
    class Approve(Feature):
        def execute(self, dto: CommandApprove) -> Answer:
            return Answer(by=security.identity.subject)

    @bus.app_service(CommandReview)
    @auth.authenticated
    class Review(ApplicationService):
        auth: AccessControl[P]

        def execute(self, dto: CommandReview) -> Answer:
            return Answer(
                by=self.auth.identity.subject,
                may_approve=self.auth.has_role("billing.manager"),
            )

    auth.on(bus)
    return bus, auth


def run(
    bus: UseFramework, dto, identity: Identity | None = None, tenant: str = "bo"
) -> Answer:
    """`dto` run on `bus` as `identity` — nobody when none — in `tenant`; what it answered."""
    with bus.context({"tenant_id": tenant}), security.as_identity(
        identity or Identity.anonymous()
    ):
        answered = bus(dto, Answer)
    assert answered is not None
    return answered


def test_what_declares_nothing_runs_for_anyone_and_says_it_is_open() -> None:
    bus, auth = billing()
    assert run(bus, QueryHealth()).by == "anonymous"
    described = auth.describe()
    assert described.use_cases[f"{__name__}.QueryHealth"] == OPEN
    assert described.every_call == ["tenant_active"]


def test_a_rule_decides_with_the_command_and_names_itself_when_refused() -> None:
    bus, _ = billing()
    assert run(bus, QueryInvoice(number="F-1", owner="user:ana"), ANA).by == "user:ana"
    with pytest.raises(PermissionDenied, match="its own invoice does not hold"):
        run(bus, QueryInvoice(number="F-2", owner="user:bea"), ANA)
    with pytest.raises(Unauthenticated):
        run(bus, QueryInvoice(number="F-1", owner="user:ana"))


def test_a_role_is_met_by_one_of_those_named() -> None:
    bus, auth = billing()
    assert run(bus, CommandApprove(number="F-1"), ANA).by == "user:ana"
    clerk = BEA.model_copy(update={"tenant": "bo"})
    with pytest.raises(PermissionDenied, match="holds no billing.manager or billing.admin"):
        run(bus, CommandApprove(number="F-1"), clerk)
    assert auth.describe().use_cases[f"{__name__}.CommandApprove"] == (
        "role billing.manager or billing.admin"
    )


def test_a_role_is_read_off_the_closure_a_catalog_gives() -> None:
    invoicing = Privilege("invoicing", "Invoicing", category=Category("billing", "Billing"))
    clerk = Role("billing.clerk", "Clerk", grants={P.READ}, privilege=invoicing, sequence=10)
    manager = Role(
        "billing.manager", "Manager", implies=(clerk,), privilege=invoicing, sequence=20
    )
    held = GrantCatalog([manager]).closure({"billing.manager"})
    auth = AccessControl[P]()
    with security.as_identity(Identity.user("user:ana", roles=held)):
        assert auth.has_role("billing.clerk")
    with security.as_identity(Identity.user("user:bea", roles={"billing.clerk"})):
        assert not auth.has_role("billing.manager")


def test_every_call_intercepts_open_and_declared_use_cases_alike() -> None:
    bus, _ = billing()
    with pytest.raises(PermissionDenied, match="tenant_active does not hold"):
        run(bus, QueryHealth(), tenant="pe")
    with pytest.raises(PermissionDenied, match="tenant_active"):
        run(bus, CommandApprove(number="F-1"), ANA, tenant="pe")
    with security.as_system("a nightly close"), bus.context({"tenant_id": "pe"}):
        assert bus(QueryHealth(), Answer).by == "system"


def test_a_check_may_answer_with_a_refusal_of_its_own() -> None:
    auth = AccessControl[P]()
    bus = UseFramework("step-billing", log_after_execution=False)

    def recent(call: AccessCall) -> bool:
        raise Unauthenticated("sign in again", step_up="mfa")

    @bus.feature(CommandApprove)
    @auth.requires(auth.rule("recent sign-in", recent))
    class Approve(Feature):
        def execute(self, dto: CommandApprove) -> Answer:
            return Answer()

    auth.on(bus)
    with pytest.raises(Unauthenticated) as refused:
        run(bus, CommandApprove(number="F-1"), ANA)
    assert refused.value.step_up == "mfa"


def test_a_handler_asks_self_auth_in_the_same_terms() -> None:
    bus, auth = billing()
    reviewed = run(bus, CommandReview(number="F-1"), ANA)
    assert reviewed.by == "user:ana" and reviewed.may_approve
    assert not run(bus, CommandReview(number="F-1"), BEA, tenant="bo").may_approve
    with security.as_identity(ANA):
        assert auth.allows(auth.role("billing.manager"))
        auth.check(P.READ)
        with pytest.raises(PermissionDenied):
            auth.check(auth.rule("never", lambda call: False))


def test_a_catalog_lists_only_what_every_call_lets_through() -> None:
    bus, auth = billing()
    with bus.context({"tenant_id": "pe"}):
        assert not auth.may_run(bus, QueryHealth, ANA)
    with bus.context({"tenant_id": "bo"}):
        assert auth.may_run(bus, QueryHealth, ANA)


def test_every_call_takes_a_name_and_answers_the_check_as_given() -> None:
    auth = AccessControl[P]()

    def maintenance(call: AccessCall) -> bool:
        return True

    assert auth.every_call(maintenance, name="not in maintenance") is maintenance
    assert [one.name for one in auth.checks] == ["not in maintenance"]


def test_any_of_mixes_a_rule_and_a_role_its_owner_or_an_admin() -> None:
    auth = AccessControl[P]()
    bus = UseFramework("owner-or-admin", log_after_execution=False)
    owner_or_admin = auth.any_of(
        auth.rule("its owner", lambda call: call.dto.owner == call.identity.subject),
        auth.role("billing.admin"),
    )

    @bus.feature(QueryInvoice)
    @auth.requires(owner_or_admin)
    class ReadInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> Answer:
            return Answer(by=security.identity.subject)

    auth.on(bus)
    admin = Identity.user("user:root", roles={"billing.admin"})
    assert run(bus, QueryInvoice(number="F-1", owner="user:ana"), ANA).by == "user:ana"
    assert run(bus, QueryInvoice(number="F-1", owner="user:ana"), admin).by == "user:root"
    with pytest.raises(PermissionDenied, match="its owner does not hold and holds no"):
        run(bus, QueryInvoice(number="F-1", owner="user:ana"), BEA)
    assert str(owner_or_admin) == "its owner or role billing.admin"
