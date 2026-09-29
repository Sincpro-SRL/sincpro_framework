"""AccessControl: what a use case or hook requires, who is asked, and what happens when it is
missing — however the use case is reached.

The incidents it protects against: a use case reachable by someone it was not meant for — by
replacing its handler, by an interceptor answering first, by writing the identity into the
context — a legitimate orchestration refused halfway, and a provider of the project's that the
guard misreads.
"""

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    AuthProvider,
    Credentials,
    Identity,
    Permission,
    PermissionDenied,
    RolePermissions,
    StaticProvider,
    Unauthenticated,
    WhenDenied,
    as_identity,
    as_system,
    current_identity,
)
from sincpro_framework.ddd import Condition, Criteria, Entity, MemoryRepository
from sincpro_framework.ddd.repositories.hooks import Hook, Hooks
from sincpro_framework.exceptions import ExtensionRefused
from sincpro_framework.testing import AuthProviderContract, RecordingProvider, granting


class BillingPermission(Permission):
    ISSUE_INVOICE = "billing.invoice.issue"
    READ_INVOICES = "billing.invoice.read"
    AUDIT = "billing.audit"
    READ_CREDIT = "billing.credit.read"
    WRITE_INVOICE = "billing.invoice.write"


class CommandIssueInvoice(DataTransferObject):
    total: int = 1


class CommandReadInvoices(DataTransferObject):
    pass


class CommandCreditReport(DataTransferObject):
    pass


class CommandCheckout(DataTransferObject):
    pass


class CommandPrice(DataTransferObject):
    pass


class CommandHealth(DataTransferObject):
    pass


class CommandMyProfile(DataTransferObject):
    pass


class Answer(DataTransferObject):
    by: str = ""
    credit: str = ""


ACCOUNTANT = Identity.user(
    "user:1",
    tenant="bo",
    permissions={BillingPermission.ISSUE_INVOICE, BillingPermission.READ_INVOICES},
)
NOBODY_SPECIAL = Identity.user("user:2", tenant="bo")


def _by(answer: Any) -> str:
    assert isinstance(answer, Answer)
    return answer.by


def _billing(auth: AccessControl[BillingPermission]) -> UseFramework:
    billing = UseFramework("billing", log_after_execution=False)

    @billing.feature(CommandIssueInvoice)
    @auth.requires(BillingPermission.ISSUE_INVOICE)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Answer:
            return Answer(by=self.auth.identity.subject)

    @billing.feature(CommandReadInvoices)
    @auth.requires(auth.any_of(BillingPermission.READ_INVOICES, BillingPermission.AUDIT))
    class ReadInvoices(Feature):
        def execute(self, dto: CommandReadInvoices) -> Answer:
            return Answer()

    @billing.feature(CommandCreditReport)
    @auth.requires(BillingPermission.READ_CREDIT, when_denied=WhenDenied.SKIP)
    class CreditReport(Feature):
        def execute(self, dto: CommandCreditReport) -> Answer:
            return Answer(credit="AAA")

    @billing.feature(CommandPrice)
    class Price(Feature):
        def execute(self, dto: CommandPrice) -> Answer:
            return Answer(by="price")

    @billing.feature(CommandHealth)
    @auth.public
    class Health(Feature):
        def execute(self, dto: CommandHealth) -> Answer:
            return Answer()

    @billing.feature(CommandMyProfile)
    @auth.authenticated
    class MyProfile(Feature):
        def execute(self, dto: CommandMyProfile) -> Answer:
            return Answer(by=current_identity().subject)

    @billing.app_service(CommandCheckout)
    @auth.requires(BillingPermission.ISSUE_INVOICE)
    class Checkout(ApplicationService):
        def execute(self, dto: CommandCheckout) -> Answer:
            self.feature_bus.execute(CommandPrice())
            credit = self.feature_bus.execute(CommandCreditReport(), Answer)
            invoice = self.feature_bus.execute(CommandIssueInvoice(), Answer)
            assert invoice is not None
            return Answer(by=invoice.by, credit=credit.credit if credit else "skipped")

    auth.on(billing)
    return billing


@pytest.fixture
def auth() -> AccessControl[BillingPermission]:
    return AccessControl[BillingPermission]()


@pytest.fixture
def billing(auth: AccessControl[BillingPermission]) -> UseFramework:
    return _billing(auth)


# What is required, and who may


def test_what_the_identity_holds_runs(billing: UseFramework) -> None:
    with as_identity(ACCOUNTANT):
        assert _by(billing(CommandIssueInvoice())) == "user:1"


def test_what_it_lacks_is_denied_naming_the_permission(billing: UseFramework) -> None:
    with as_identity(NOBODY_SPECIAL), pytest.raises(PermissionDenied) as refused:
        billing(CommandIssueInvoice())
    assert refused.value.requirement == BillingPermission.ISSUE_INVOICE
    assert refused.value.subject == "user:2"


def test_nobody_is_asked_to_authenticate(billing: UseFramework) -> None:
    with pytest.raises(Unauthenticated):
        billing(CommandIssueInvoice())


def test_any_of_is_met_by_one(billing: UseFramework) -> None:
    with granting(BillingPermission.AUDIT):
        billing(CommandReadInvoices())
    with as_identity(NOBODY_SPECIAL), pytest.raises(PermissionDenied):
        billing(CommandReadInvoices())


def test_public_needs_nobody_and_authenticated_needs_anybody(billing: UseFramework) -> None:
    billing(CommandHealth())
    with pytest.raises(Unauthenticated):
        billing(CommandMyProfile())
    with as_identity(NOBODY_SPECIAL):
        assert _by(billing(CommandMyProfile())) == "user:2"


# Skipping


def test_what_declares_nothing_is_not_checked(billing: UseFramework) -> None:
    assert _by(billing(CommandPrice())) == "price"


def test_a_strict_bus_refuses_what_declares_nothing_entered_from_outside() -> None:
    billing = _billing(AccessControl[BillingPermission](strict=True))
    with as_identity(ACCOUNTANT):
        with pytest.raises(PermissionDenied):
            billing(CommandPrice())
        billing(CommandHealth())


def test_disabled_checks_nothing() -> None:
    billing = _billing(AccessControl[BillingPermission](enabled=False))
    assert _by(billing(CommandIssueInvoice())) == "anonymous"


def test_an_optional_step_is_skipped_not_failed(billing: UseFramework) -> None:
    with as_identity(ACCOUNTANT):
        answer = billing(CommandCheckout())
    assert isinstance(answer, Answer)
    assert (answer.by, answer.credit) == ("user:1", "skipped")
    with as_identity(ACCOUNTANT), pytest.raises(AssertionError):
        assert billing(CommandCreditReport()) is not None


def test_an_orchestrator_asks_before_an_optional_step(
    auth: AccessControl[BillingPermission],
) -> None:
    with as_identity(ACCOUNTANT):
        assert auth.allows(BillingPermission.ISSUE_INVOICE)
        assert not auth.allows(BillingPermission.READ_CREDIT)


def test_the_system_meets_every_requirement(billing: UseFramework) -> None:
    with as_system("cron: close cash registers"):
        billing(CommandIssueInvoice())
        assert billing(CommandCreditReport()) == Answer(credit="AAA")


# Nesting and replacing


def test_an_orchestration_is_decided_once_and_nested_use_cases_inherit(
    billing: UseFramework,
) -> None:
    strict = _billing(AccessControl[BillingPermission](strict=True))
    with as_identity(ACCOUNTANT):
        assert _by(billing(CommandCheckout())) == "user:1"
        assert _by(strict(CommandCheckout())) == "user:1"


def test_a_nested_use_case_that_declares_its_own_permission_is_checked_again() -> None:
    auth = AccessControl[BillingPermission]()
    billing = UseFramework("billing", log_after_execution=False)

    @billing.feature(CommandReadInvoices)
    @auth.requires(BillingPermission.AUDIT)
    class ReadInvoices(Feature):
        def execute(self, dto: CommandReadInvoices) -> Answer:
            return Answer()

    @billing.app_service(CommandCheckout)
    @auth.requires(BillingPermission.ISSUE_INVOICE)
    class Checkout(ApplicationService):
        def execute(self, dto: CommandCheckout) -> Answer | None:
            return self.feature_bus.execute(CommandReadInvoices(), Answer)

    auth.on(billing)
    with as_identity(ACCOUNTANT), pytest.raises(PermissionDenied) as refused:
        billing(CommandCheckout())
    assert refused.value.requirement == BillingPermission.AUDIT


def test_a_replacement_that_declares_nothing_keeps_what_it_replaces_required() -> None:
    auth = AccessControl[BillingPermission]()
    billing = UseFramework("billing", log_after_execution=False)

    @billing.feature(CommandIssueInvoice)
    @auth.requires(BillingPermission.ISSUE_INVOICE)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Answer:
            return Answer(by="core")

    @billing.feature(CommandIssueInvoice, replaces=IssueInvoice)
    class IssueInvoiceInBolivia(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Answer:
            return Answer(by="bolivia")

    auth.on(billing)
    with as_identity(NOBODY_SPECIAL), pytest.raises(PermissionDenied):
        billing(CommandIssueInvoice())
    with as_identity(ACCOUNTANT):
        assert _by(billing(CommandIssueInvoice())) == "bolivia"


# What must not open a hole


def test_an_interceptor_that_answers_by_itself_never_answers_before_the_guard() -> None:
    auth = AccessControl[BillingPermission]()
    billing = UseFramework("billing", log_after_execution=False)

    @billing.feature(CommandIssueInvoice)
    @auth.requires(BillingPermission.ISSUE_INVOICE)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Answer:
            return Answer(by="handler")

    @billing.interceptor(CommandIssueInvoice, sequence=-1_000)
    def cached(dto: CommandIssueInvoice, call_next: Any) -> Answer:
        return Answer(by="cache")

    auth.on(billing)
    with pytest.raises(Unauthenticated):
        billing(CommandIssueInvoice())


def test_an_identity_written_into_the_context_is_not_who_acts(billing: UseFramework) -> None:
    with billing.context({"identity": ACCOUNTANT}) as forged, pytest.raises(Unauthenticated):
        forged(CommandIssueInvoice())


def test_who_acts_follows_the_execution_into_a_thread(billing: UseFramework) -> None:
    seen: list[str] = []
    billing(CommandHealth())
    assert billing.bus is not None
    with as_identity(ACCOUNTANT):
        handle = billing.bus.feature_bus.thread_context()
        worker = threading.Thread(
            target=lambda: seen.append(_by(handle.execute(CommandMyProfile())))
        )
        worker.start()
        worker.join()
    assert seen == ["user:1"]


def test_declared_both_public_and_required_is_refused(
    auth: AccessControl[BillingPermission],
) -> None:
    with pytest.raises(ExtensionRefused):

        @auth.public
        @auth.requires(BillingPermission.READ_INVOICES)
        class Both(Feature):
            def execute(self, dto: Any) -> None: ...


# Providers


class _HeaderKeys(AuthProvider):
    """A provider of the project's: an API key header, permissions asked of an API."""

    name = "keys"

    def __init__(self, keys: Mapping[str, str], api: set[tuple[str, str]]) -> None:
        self.keys = keys
        self.api = api

    def authenticate(self, credentials: Credentials) -> Identity | None:
        key = credentials.headers.get("x-api-key")
        if key is None:
            return None
        if key not in self.keys:
            raise Unauthenticated("unknown key")
        return Identity.user(self.keys[key], tenant="bo")

    def has_permission(
        self,
        identity: Identity,
        permission: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        if resource is not None and getattr(resource, "total", 0) > 10_000:
            raise PermissionDenied(identity.subject, permission, "above the limit")
        return (identity.subject, permission) in self.api

    def scope(self, identity: Identity, aggregate: type) -> Criteria | None:
        return Criteria(where=Condition(field="tenant", value=identity.tenant or ""))

    def challenge(self) -> str | None:
        return 'ApiKey realm="billing"'


@dataclass
class Invoice(Entity):
    total: int = 0
    tenant: str = "bo"


def _keys() -> _HeaderKeys:
    return _HeaderKeys({"k-1": "user:1"}, {("user:1", BillingPermission.ISSUE_INVOICE)})


def test_the_first_provider_that_recognizes_the_credentials_answers() -> None:
    static = StaticProvider({"token-a": Identity.user("user:9")})
    auth = AccessControl[BillingPermission](providers=[static, _keys()])
    by_key = auth.authenticate(Credentials(transport="http", headers={"X-Api-Key": "k-1"}))
    assert (by_key.subject, by_key.provider) == ("user:1", "keys")
    by_token = auth.authenticate(
        Credentials(transport="http", headers={"Authorization": "Bearer token-a"})
    )
    assert (by_token.subject, by_token.provider) == ("user:9", "static")
    assert auth.authenticate(Credentials(transport="http")).is_anonymous
    with pytest.raises(Unauthenticated):
        auth.authenticate(Credentials(transport="http", headers={"x-api-key": "stolen"}))


def test_the_identitys_own_provider_is_asked_with_the_resource() -> None:
    recording = RecordingProvider(_keys())
    auth = AccessControl[BillingPermission](providers=[recording])
    billing = _billing(auth)
    identity = auth.authenticate(Credentials(transport="http", headers={"x-api-key": "k-1"}))
    with as_identity(identity):
        billing(CommandIssueInvoice())
        with pytest.raises(PermissionDenied) as refused:
            auth.check(BillingPermission.ISSUE_INVOICE, Invoice(total=50_000))
    assert refused.value.reason == "above the limit"
    assert ("has_permission", "user:1", "billing.invoice.issue") in recording.asked


def test_a_provider_filters_what_an_identity_reads() -> None:
    auth = AccessControl[BillingPermission](providers=[_keys()])
    invoices = MemoryRepository(Invoice(total=1, tenant="bo"), Invoice(total=2, tenant="pe"))
    with as_identity(
        Identity.user("user:1", tenant="bo").model_copy(update={"provider": "keys"})
    ):
        scope = auth.scope_of(Invoice)
        assert scope is not None
        assert [one.tenant for one in invoices.search(Invoice, scope)] == ["bo"]


def test_a_401_says_where_to_get_a_credential() -> None:
    auth = AccessControl[BillingPermission](providers=[StaticProvider({}), _keys()])
    assert auth.challenges() == ['ApiKey realm="billing"']


def test_credentials_never_print_their_values() -> None:
    credentials = Credentials(transport="http", headers={"Authorization": "Bearer secret"})
    assert credentials.bearer == "secret"
    assert "secret" not in repr(credentials) and "secret" not in str(credentials)


def test_a_role_grants_what_the_roles_it_implies_grant() -> None:
    grants = RolePermissions(
        {"accountant": {BillingPermission.ISSUE_INVOICE}, "admin": {BillingPermission.AUDIT}},
        implies={"admin": {"accountant"}},
    )
    admin = grants.resolve(Identity.user("user:3", roles={"admin"}))
    assert admin.permissions == {BillingPermission.ISSUE_INVOICE, BillingPermission.AUDIT}


class TestStaticProvider(AuthProviderContract):
    def make_provider(self) -> AuthProvider:
        return StaticProvider({"token-a": ACCOUNTANT})

    def accepted(self) -> Credentials:
        return Credentials(transport="http", headers={"authorization": "Bearer token-a"})

    def foreign(self) -> Credentials:
        return Credentials(transport="http", headers={"x-api-key": "k-1"})

    def rejected(self) -> Credentials:
        return Credentials(transport="http", headers={"authorization": "Bearer forged"})

    def granted_permission(self) -> str | None:
        return BillingPermission.ISSUE_INVOICE


class TestAProvidersOwnWrapper(AuthProviderContract):
    def make_provider(self) -> AuthProvider:
        return _keys()

    def accepted(self) -> Credentials:
        return Credentials(transport="http", headers={"x-api-key": "k-1"})

    def foreign(self) -> Credentials:
        return Credentials(transport="http", headers={"authorization": "Bearer token-a"})

    def rejected(self) -> Credentials:
        return Credentials(transport="http", headers={"x-api-key": "stolen"})


# Hooks


def test_a_hook_that_requires_a_permission_refuses_the_write() -> None:
    auth = AccessControl[BillingPermission]()
    hooks = Hooks(None)
    ran: list[str] = []

    @hooks.on(Invoice)
    @auth.requires(BillingPermission.WRITE_INVOICE)
    class GuardsInvoices(Hook):
        def before_save(self, invoice: Invoice) -> None:
            ran.append("guarded")

    auth.on(hooks)
    invoices = MemoryRepository(hooks=hooks)
    with as_identity(ACCOUNTANT), pytest.raises(PermissionDenied):
        invoices.save(Invoice(total=1))
    with granting(BillingPermission.WRITE_INVOICE):
        invoices.save(Invoice(total=1))
    assert ran == ["guarded"]


def test_a_hook_that_skips_when_denied_leaves_its_moment_out() -> None:
    auth = AccessControl[BillingPermission]()
    hooks = Hooks(None)
    audited: list[int] = []

    @hooks.on(Invoice)
    @auth.requires(BillingPermission.AUDIT, when_denied=WhenDenied.SKIP)
    class Audits(Hook):
        def after_save(self, invoice: Invoice) -> None:
            audited.append(invoice.total)

    auth.on(hooks)
    invoices = MemoryRepository(hooks=hooks)
    with as_identity(ACCOUNTANT):
        invoices.save(Invoice(total=1))
    with granting(BillingPermission.AUDIT):
        invoices.save(Invoice(total=2))
    assert audited == [2]


# What it says about itself


def test_verify_names_a_declaration_that_never_runs_and_what_strict_refuses() -> None:
    auth = AccessControl[BillingPermission](strict=True)
    billing = _billing(auth)

    @auth.requires(BillingPermission.AUDIT)
    class NeverRegistered(Feature):
        def execute(self, dto: Any) -> None: ...

    problems = auth.verify()
    assert any("NeverRegistered" in one for one in problems)
    assert any("CommandPrice" in one and "strict" in one for one in problems)
    assert billing is not None


def test_describe_and_requirements_of_say_what_each_use_case_declared(
    auth: AccessControl[BillingPermission], billing: UseFramework
) -> None:
    described = auth.describe()
    by_command = {
        name.rsplit(".", 1)[-1]: needed for name, needed in described.use_cases.items()
    }
    assert by_command["CommandIssueInvoice"] == "billing.invoice.issue"
    assert by_command["CommandHealth"] == "public"
    assert by_command["CommandPrice"] == "unchecked"
    assert by_command["CommandCreditReport"] == "billing.credit.read (else skipped)"
    declared = auth.requirements_of(CommandIssueInvoice)
    assert declared is not None and declared.requirements == (
        BillingPermission.ISSUE_INVOICE,
    )


# What the review of phase 1 found open — each closed, each kept closed


def _guarded_core_hooks(auth: AccessControl[BillingPermission]) -> tuple[Hooks, type[Hook]]:
    core = Hooks(None)

    @core.on(Invoice)
    @auth.requires(BillingPermission.WRITE_INVOICE)
    class CoreCheck(Hook):
        def before_save(self, invoice: Invoice) -> None: ...

    return core, CoreCheck


def test_a_replacement_made_in_a_combination_keeps_what_it_replaces_required() -> None:
    auth = AccessControl[BillingPermission]()
    core, core_check = _guarded_core_hooks(auth)
    client = Hooks(None)

    @client.on(Invoice, replaces=core_check)
    class ClientCheck(Hook):
        def before_save(self, invoice: Invoice) -> None: ...

    auth.on(core)
    invoices = MemoryRepository(hooks=core.combined_with(client))
    with pytest.raises(Unauthenticated):
        invoices.save(Invoice(total=1))
    with granting(BillingPermission.WRITE_INVOICE):
        invoices.save(Invoice(total=1))


def test_a_copy_made_before_the_collection_is_guarded_is_guarded_too() -> None:
    auth = AccessControl[BillingPermission]()
    core, _ = _guarded_core_hooks(auth)
    invoices = MemoryRepository(hooks=core.without())
    auth.on(core)
    with pytest.raises(Unauthenticated):
        invoices.save(Invoice(total=1))


def test_a_collection_guarded_twice_is_refused_and_a_combination_asks_once() -> None:
    recording = RecordingProvider()
    auth = AccessControl[BillingPermission](providers=[recording])
    core, _ = _guarded_core_hooks(auth)
    client = Hooks(None)
    auth.on(core)
    auth.on(client)
    with pytest.raises(ExtensionRefused):
        auth.on(core)
    writer = Identity.user("user:1", permissions={BillingPermission.WRITE_INVOICE})
    with as_identity(writer.model_copy(update={"provider": recording.name})):
        MemoryRepository(hooks=core.combined_with(client)).save(Invoice(total=1))
    assert [one for one in recording.asked if one[0] == "has_permission"] == [
        ("has_permission", "user:1", BillingPermission.WRITE_INVOICE)
    ]


def test_reads_scoped_by_identity_are_refused_to_nobody() -> None:
    auth = AccessControl[BillingPermission](providers=[_keys()])
    with pytest.raises(Unauthenticated):
        auth.scope_of(Invoice)
    with as_identity(Identity.user("user:1").model_copy(update={"provider": "elsewhere"})):
        with pytest.raises(ExtensionRefused):
            auth.scope_of(Invoice)


def test_one_resource_refused_with_a_reason_never_takes_a_batch_down() -> None:
    auth = AccessControl[BillingPermission](providers=[_keys()])
    identity = auth.authenticate(Credentials(transport="http", headers={"x-api-key": "k-1"}))
    with as_identity(identity):
        allowed = auth.permitted(
            BillingPermission.ISSUE_INVOICE, [Invoice(total=1), Invoice(total=50_000)]
        )
    assert allowed == [True, False]


def test_describing_and_verifying_build_nothing_and_close_nothing() -> None:
    auth = AccessControl[BillingPermission]()
    billing = UseFramework("billing", log_after_execution=False)
    hooks = Hooks(None)
    auth.on(billing)
    auth.on(hooks)
    auth.verify()
    auth.describe()
    auth.requirements_of(CommandIssueInvoice)

    @billing.feature(CommandIssueInvoice)
    @auth.requires(BillingPermission.ISSUE_INVOICE)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Answer:
            return Answer()

    @hooks.on(Invoice)
    class LateButOnTime(Hook):
        def before_save(self, invoice: Invoice) -> None: ...

    assert billing.bus is None
    declared = auth.requirements_of(CommandIssueInvoice)
    assert declared is not None and declared.requirements == (
        BillingPermission.ISSUE_INVOICE,
    )


def test_nobody_calling_a_strict_bus_is_asked_to_authenticate() -> None:
    billing = _billing(AccessControl[BillingPermission](strict=True))
    with pytest.raises(Unauthenticated):
        billing(CommandPrice())


# The system, a disabled control, and what a wiring mistake raises


def test_the_system_is_granted_every_check_scope_and_batch() -> None:
    auth = AccessControl[BillingPermission](providers=[_keys()])
    with as_system("migration: backfill invoices"):
        assert auth.allows(BillingPermission.AUDIT)
        auth.check(BillingPermission.AUDIT, Invoice(total=50_000))
        assert auth.permitted(BillingPermission.AUDIT, [Invoice(), Invoice()]) == [True, True]
        assert auth.scope_of(Invoice) is None


def test_nobody_is_refused_a_check_with_unauthenticated(
    auth: AccessControl[BillingPermission],
) -> None:
    with pytest.raises(Unauthenticated):
        auth.check(BillingPermission.ISSUE_INVOICE)
    assert auth.permitted(BillingPermission.ISSUE_INVOICE, [None, None]) == [False, False]


def test_an_identity_opened_by_hand_answers_with_its_own_permissions(
    auth: AccessControl[BillingPermission],
) -> None:
    with granting(BillingPermission.AUDIT):
        assert auth.permitted(BillingPermission.AUDIT, [None, None]) == [True, True]
        assert auth.scope_of(Invoice) is None


def test_a_provider_that_refuses_a_whole_batch_refuses_each() -> None:
    class RefusesBatches(_HeaderKeys):
        def permitted(self, identity, permission, resources, context=None) -> list[bool]:
            raise PermissionDenied(identity.subject, permission, "no batch for you")

    auth = AccessControl[BillingPermission](
        providers=[RefusesBatches({"k-1": "user:1"}, set())]
    )
    with as_identity(
        auth.authenticate(Credentials(transport="http", headers={"x-api-key": "k-1"}))
    ):
        assert auth.permitted(BillingPermission.AUDIT, [None, None]) == [False, False]


def test_a_hook_is_not_checked_for_the_system_nor_when_disabled() -> None:
    for auth, acting in (
        (AccessControl[BillingPermission](), as_system("cron")),
        (AccessControl[BillingPermission](enabled=False), as_identity(NOBODY_SPECIAL)),
    ):
        core, _ = _guarded_core_hooks(auth)
        auth.on(core)
        with acting:
            MemoryRepository(hooks=core).save(Invoice(total=1))


def test_the_call_to_another_service_carries_what_the_identitys_provider_issues() -> None:
    issued = Credentials(transport="http", headers={"authorization": "Bearer for-stock"})

    class IssuesTokens(_HeaderKeys):
        def credentials_for(self, identity: Identity) -> Credentials | None:
            return issued

    auth = AccessControl[BillingPermission](
        providers=[StaticProvider({}), IssuesTokens({"k-1": "user:1"}, set())]
    )
    identity = auth.authenticate(Credentials(transport="http", headers={"x-api-key": "k-1"}))
    with as_identity(identity):
        assert auth.credentials_for() is issued
    assert AccessControl[BillingPermission]().credentials_for(Identity.user("user:1")) is None


def test_wiring_mistakes_are_refused_where_they_are_made() -> None:
    auth = AccessControl[BillingPermission]()
    billing = UseFramework("billing", log_after_execution=False)
    auth.on(billing)
    with pytest.raises(ExtensionRefused):
        auth.on(billing)
    with pytest.raises(ExtensionRefused):
        auth.requires()
    with pytest.raises(ExtensionRefused):

        @auth.requires(BillingPermission.AUDIT, when_denied=WhenDenied.SKIP)
        @auth.requires(BillingPermission.READ_INVOICES)
        class TwoMinds(Feature):
            def execute(self, dto: Any) -> None: ...


class _TokenClaims(DataTransferObject):
    company: str


def test_the_rest_of_the_credential_is_read_typed() -> None:
    identity = Identity.service("stock").model_copy(update={"claims": {"company": "bo"}})
    assert identity.subject == "service:stock"
    assert identity.claims_as(_TokenClaims).company == "bo"
