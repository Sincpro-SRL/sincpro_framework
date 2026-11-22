"""A bounded context reached in process — a bus injected as a dependency, guarded by its own
`AccessControl` — decides about the caller another context let in, as that context's provider
says.

The incident: `sales` authenticated ana with its provider and called `billing` in process;
billing's `AccessControl` held no provider of that name and answered a `ProgrammingError` — a
500 in the middle of the request, for an identity the same process had verified a moment before.
"""

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
