"""A bounded context guarded by an `AccessControl`, and use cases stored as source that declare
their own access — what the runtime registry loads onto each new generation of the bus."""

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import AccessControl, Permission


class BillingPermission(Permission):
    TAX = "billing.tax.compute"
    QUOTE = "billing.quote.read"
    QUOTE_V2 = "billing.quote.read.v2"


class CommandComputeTax(DataTransferObject):
    amount: int


class ResponseComputeTax(DataTransferObject):
    tax: int


auth = AccessControl[BillingPermission]()
billing = UseFramework("guarded-billing", log_after_execution=False)


@billing.feature(CommandComputeTax)
@auth.requires(BillingPermission.TAX)
class ComputeTax(Feature):
    def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
        return ResponseComputeTax(tax=dto.amount // 10)


auth.on(billing)


def quote(requirement: str) -> str:
    """A stored use case whose access is `requirement` — a decorator line, or nothing."""
    return f"""
from sincpro_framework import DataTransferObject, Feature

from tests.auth.guarded_billing import BillingPermission, auth


class CommandQuote(DataTransferObject):
    amount: int


class ResponseQuote(DataTransferObject):
    total: int


{requirement}
class Quote(Feature):
    def execute(self, dto: CommandQuote) -> ResponseQuote:
        return ResponseQuote(total=dto.amount * 2)
"""


PUBLIC_TAX = """
from sincpro_framework import Feature

from tests.auth.guarded_billing import CommandComputeTax, ResponseComputeTax, auth


@auth.public
class ComputeTaxForEveryone(Feature):
    def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
        return ResponseComputeTax(tax=0)
"""
