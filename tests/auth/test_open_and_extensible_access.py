"""Open unless it says otherwise, and said in the project's own terms: a use case that declares
nothing runs for anyone; `requires` takes a permission, a role (Odoo's groups, read off their
closure) or a rule of the project's own, asked with the call and the execution's context;
`every_call` is an interceptor of the project's, asked on every call, open or declared; and a
handler asks the same things of `self.auth`."""

import pytest
from structlog.testing import capture_logs

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    OPEN,
    AccessCall,
    AccessControl,
    AuthError,
    Identity,
    Permission,
    PermissionDenied,
    Unauthenticated,
    WhenDenied,
    security,
)
from sincpro_framework.auth.grants import Category, GrantCatalog, Privilege, Role


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
        "role billing.manager or role billing.admin"
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


def test_a_rule_asked_with_no_command_is_refused_never_a_crash() -> None:
    bus, auth = billing()
    assert not auth.may_run(bus, QueryInvoice, ANA)  # `call.dto.owner` cannot be read
    with security.as_identity(ANA):
        assert not auth.allows(auth.rule("its own", lambda call: call.dto.owner == "x"))
        lenient = auth.rule("its own", lambda call: call.dto is None or call.dto.owner == "x")
        assert auth.allows(lenient)


def test_every_call_is_raised_even_where_the_declaration_says_skip() -> None:
    auth = AccessControl[P]()
    bus = UseFramework("skip-billing", log_after_execution=False)
    auth.every_call(lambda call: call.context.get("tenant_id") != "pe", name="tenant_active")

    @bus.feature(QueryHealth)
    @auth.requires(P.READ, when_denied=WhenDenied.SKIP)
    class Health(Feature):
        def execute(self, dto: QueryHealth) -> Answer:
            return Answer(by="ran")

    auth.on(bus)
    with pytest.raises(PermissionDenied, match="tenant_active"):
        run(bus, QueryHealth(), ANA, tenant="pe")


def test_any_of_answers_a_step_up_when_one_would_meet_it() -> None:
    auth = AccessControl[P]()
    with security.as_identity(BEA), pytest.raises(Unauthenticated) as refused:
        auth.check(auth.any_of(auth.assurance(methods={"mfa"}), P.APPROVE))
    assert refused.value.step_up == "mfa"


def test_a_refusal_of_the_projects_own_without_a_reason_is_still_worded() -> None:
    class Closed(AuthError):
        http_status = 403

    def closed(call: AccessCall) -> bool:
        raise Closed("the ledger is closed")

    auth = AccessControl[P]()
    with security.as_identity(BEA), pytest.raises(PermissionDenied, match="ledger is closed"):
        auth.check(auth.any_of(auth.rule("open ledger", closed), P.APPROVE))


def test_by_field_with_no_command_keeps_roles_and_rules_among_its_options() -> None:
    auth = AccessControl[P]()
    chosen = auth.by_field("number", {"in": auth.role("billing.clerk"), "out": P.APPROVE})
    with security.as_identity(BEA):
        assert auth.allows(chosen)


def test_a_rule_named_open_is_made_with_a_warning_and_a_test_copy_never_adds_checks() -> None:
    auth = AccessControl[P]()
    with capture_logs() as logs:
        assert auth.rule("open", lambda call: True).name == "open"
    assert any("reads as a use case that declares nothing" in line["event"] for line in logs)
    copy = auth.using([])
    copy.every_call(lambda call: False, name="the test's own")
    assert auth.checks == [] and [one.name for one in copy.checks] == ["the test's own"]


def test_by_field_hands_the_command_to_the_rule_it_chooses() -> None:
    auth = AccessControl[P]()
    bus = UseFramework("by-field-rule", log_after_execution=False)
    own_invoice = auth.rule(
        "its own invoice", lambda call: call.dto.owner == call.identity.subject
    )

    @bus.feature(QueryInvoice)
    @auth.requires(
        auth.by_field("number", {"F-1": auth.any_of(own_invoice), "F-2": own_invoice})
    )
    class ReadInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> Answer:
            return Answer(by=security.identity.subject)

    auth.on(bus)
    assert run(bus, QueryInvoice(number="F-1", owner="user:ana"), ANA).by == "user:ana"
    assert run(bus, QueryInvoice(number="F-2", owner="user:ana"), ANA).by == "user:ana"
    with pytest.raises(PermissionDenied, match="its own invoice does not hold"):
        run(bus, QueryInvoice(number="F-2", owner="user:bea"), ANA)


def test_nobody_meets_no_requirement_by_hand_as_the_guard_decides() -> None:
    class Invoice:
        pass

    auth = AccessControl[P]()
    always = auth.rule("always", lambda call: True)
    assert not auth.allows(always)
    with pytest.raises(Unauthenticated) as refused:
        auth.check(always)
    assert refused.value.anonymous
    with pytest.raises(Unauthenticated) as unscoped:
        auth.scope_of(Invoice)
    assert unscoped.value.anonymous


def test_access_control_turned_off_positionally_says_so() -> None:
    """`enabled` is the second parameter: turning it off — by name or by position — checks
    nothing, and `verify()` says it first."""
    assert AccessControl[P]((), False).verify() == ["access control is disabled"]


def test_any_of_grants_at_the_first_member_that_holds_and_asks_none_after() -> None:
    auth = AccessControl[P]()
    bus = UseFramework("first-holds", log_after_execution=False)

    def owner(call: AccessCall) -> bool:
        return call.resource.owner == call.identity.subject  # no resource in the guard

    @bus.feature(CommandApprove)
    @auth.requires(auth.any_of(auth.role("billing.admin"), auth.rule("its owner", owner)))
    class Approve(Feature):
        def execute(self, dto: CommandApprove) -> Answer:
            return Answer(by=security.identity.subject)

    auth.on(bus)
    admin = Identity.user("user:root", roles={"billing.admin"})
    assert run(bus, CommandApprove(number="F-1"), admin).by == "user:root"


def test_by_field_with_no_command_keeps_two_rules_of_one_name() -> None:
    auth = AccessControl[P]()
    payer = auth.rule("its party", lambda call: call.identity.subject == "user:ana")
    payee = auth.rule("its party", lambda call: call.identity.subject == "user:bea")
    chosen = auth.by_field("number", {"in": payer, "out": payee})
    with security.as_identity(BEA):
        assert auth.allows(chosen)


def test_requires_never_reads_as_open_or_authenticated() -> None:
    class Reserved(Permission):
        OPEN = "open"
        AUTHENTICATED = "authenticated"

    auth = AccessControl[Reserved]()
    for one in (Reserved.OPEN, Reserved.AUTHENTICATED):
        with capture_logs() as logs:

            @auth.requires(one)
            class Declared(Feature):
                def execute(self, dto: CommandApprove) -> None: ...

        assert auth.requirements_of(Declared) is not None
        assert any("reads as what a use case" in line["event"] for line in logs)


def test_the_openapi_of_an_open_operation_an_every_call_check_may_refuse() -> None:
    from sincpro_framework.entrypoints.adapters.rest.openapi import operation_security

    bus, auth = billing()
    assert operation_security(bus, QueryHealth) == {
        "security": [{}],
        "x-sincpro-access": OPEN,
    }
    auth.checks.clear()
    assert operation_security(bus, QueryHealth) == {"security": [], "x-sincpro-access": OPEN}
