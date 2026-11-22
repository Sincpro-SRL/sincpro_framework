"""A bounded context reached in process — a bus injected as a dependency, guarded by its own
`AccessControl` — decides about the caller another context let in, as that context's provider
says.

The incident: `sales` authenticated ana with its provider and called `billing` in process;
billing's `AccessControl` held no provider of that name and answered a `ProgrammingError` — a
500 in the middle of the request, for an identity the same process had verified a moment before.
"""

from structlog.testing import capture_logs

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    AuthProvider,
    Credentials,
    Identity,
    Permission,
    security,
)
from sincpro_framework.auth.server import StaticProvider
from sincpro_framework.ddd.criteria import Condition, Criteria


class SalesPermission(Permission):
    SELL = "sales.order.sell"


class BillingPermission(Permission):
    ISSUE = "billing.invoice.issue"


class CommandSell(DataTransferObject):
    total: int


class CommandIssue(DataTransferObject):
    total: int


class Issued(DataTransferObject):
    by: str
    scope: str = ""


class Invoice:
    pass


ANA = Identity.user(
    "user:ana", tenant="bo", permissions={SalesPermission.SELL, BillingPermission.ISSUE}
)
BEARER = Credentials(transport="http", headers={"authorization": "Bearer ana"})


class TenantScoped(StaticProvider):
    """The provider that let ana in: it scopes every read to her tenant."""

    def scope(self, identity: Identity, aggregate: type) -> Criteria | None:
        return Criteria(selection=Condition(field="tenant", value=identity.tenant or ""))


def _sales_calling_billing(billing_providers: list[AuthProvider]) -> UseFramework:
    billing = UseFramework("billing-in-process", log_after_execution=False)
    sales = UseFramework("sales-in-process", log_after_execution=False)
    sales_auth = AccessControl[SalesPermission](
        providers=[TenantScoped({"ana": ANA}, "sales")]
    )
    billing_auth = AccessControl[BillingPermission](providers=billing_providers)

    @billing.feature(CommandIssue)
    @billing_auth.requires(BillingPermission.ISSUE)
    class Issue(Feature):
        auth: AccessControl[BillingPermission]

        def execute(self, dto: CommandIssue) -> Issued:
            return Issued(
                by=security.identity.subject, scope=repr(self.auth.scope_of(Invoice))
            )

    @sales.app_service(CommandSell)
    @sales_auth.requires(SalesPermission.SELL)
    class Sell(ApplicationService):
        billing: UseFramework

        def execute(self, dto: CommandSell) -> Issued:
            issued = self.billing(CommandIssue(total=dto.total), Issued)
            assert issued is not None
            return issued

    billing_auth.on(billing)
    sales_auth.on(sales)
    sales.add_dependency("billing", billing)
    return sales


def _sell(sales: UseFramework) -> Issued:
    with security.authenticated_as(sales, BEARER):
        issued = sales(CommandSell(total=10), Issued)
    assert isinstance(issued, Issued)
    return issued


def test_a_context_with_no_provider_decides_as_the_one_that_let_the_caller_in():
    issued = _sell(_sales_calling_billing([]))
    assert issued.by == "user:ana"
    assert "bo" in issued.scope


def test_a_context_with_providers_of_its_own_still_decides_about_the_caller():
    issued = _sell(_sales_calling_billing([StaticProvider({}, name="billing-keys")]))
    assert issued.by == "user:ana"
    assert "bo" in issued.scope


class OwnKeys(StaticProvider):
    """Another kind of provider, given the name another context's provider already has."""


def test_providers_of_different_kinds_under_one_name_are_said_once():
    sales_bus = UseFramework("sales-named-alike", log_after_execution=False)
    billing_bus = UseFramework("billing-named-alike", log_after_execution=False)
    for bus in (sales_bus, billing_bus):

        @bus.feature(CommandIssue)
        class Answer(Feature):
            def execute(self, dto: CommandIssue) -> Issued:
                return Issued(by=security.identity.subject)

    billing_auth = AccessControl[BillingPermission](providers=[OwnKeys({}, name="keys")])
    with capture_logs() as logs:
        AccessControl[SalesPermission](providers=[StaticProvider({}, name="keys")]).on(
            sales_bus
        )
        billing_auth.on(billing_bus)
        sales_bus(CommandIssue(total=1))
        billing_bus(CommandIssue(total=1))
    with capture_logs() as again:
        billing_bus.fresh()(CommandIssue(total=1))

    def clashes(lines: list) -> list[str]:
        return [
            one["provider"]
            for one in lines
            if "different kinds under one name" in one["event"]
        ]

    assert "keys" in clashes(logs)
    assert clashes(again) == []


def test_one_kind_of_provider_in_every_context_never_clashes():
    first = UseFramework("first-same-kind", log_after_execution=False)
    second = UseFramework("second-same-kind", log_after_execution=False)
    for bus in (first, second):

        @bus.feature(CommandIssue)
        class Answer(Feature):
            def execute(self, dto: CommandIssue) -> Issued:
                return Issued(by=security.identity.subject)

    with capture_logs() as logs:
        for bus in (first, second):
            AccessControl[SalesPermission](providers=[StaticProvider({}, "tokens")]).on(bus)
        first(CommandIssue(total=1))
        second(CommandIssue(total=1))
    assert not [line for line in logs if "different kinds under one name" in line["event"]]
