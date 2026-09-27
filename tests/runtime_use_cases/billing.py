"""A bounded context written in code, and use cases stored as source that load onto it."""

from decimal import Decimal

from sincpro_framework import DataTransferObject, Feature, UseFramework


class CommandComputeTax(DataTransferObject):
    amount: Decimal


class ResponseComputeTax(DataTransferObject):
    tax: Decimal


billing = UseFramework("billing", log_after_execution=False)


@billing.feature(CommandComputeTax)
class ComputeTax(Feature):
    def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
        return ResponseComputeTax(tax=dto.amount * Decimal("0.13"))


QUOTE = """
from decimal import Decimal

from sincpro_framework import DataTransferObject, Feature


class CommandQuote(DataTransferObject):
    amount: Decimal


class ResponseQuote(DataTransferObject):
    total: Decimal


class Quote(Feature):
    def execute(self, dto: CommandQuote) -> ResponseQuote:
        return ResponseQuote(total=dto.amount * Decimal("1.13"))
"""

CHECKOUT = """
from decimal import Decimal

from sincpro_framework import ApplicationService, DataTransferObject

from tests.runtime_use_cases.billing import CommandComputeTax


class CommandCheckout(DataTransferObject):
    amount: Decimal


class ResponseCheckout(DataTransferObject):
    total: Decimal


class Checkout(ApplicationService):
    def execute(self, dto: CommandCheckout) -> ResponseCheckout:
        tax = self.feature_bus.execute(CommandComputeTax(amount=dto.amount)).tax
        return ResponseCheckout(total=dto.amount + tax)
"""

TAX_WITH_EXEMPTION = """
from decimal import Decimal

from sincpro_framework import Feature

from tests.runtime_use_cases.billing import CommandComputeTax, ResponseComputeTax


class ComputeTaxWithExemption(Feature):
    def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
        rate = Decimal("0") if dto.amount < 100 else Decimal("0.13")
        return ResponseComputeTax(tax=dto.amount * rate)
"""

DOUBLE_QUOTE = """
from decimal import Decimal

from sincpro_framework import ApplicationService, DataTransferObject

from sincpro_runtime.billing.quote import CommandQuote


class CommandDoubleQuote(DataTransferObject):
    amount: Decimal


class DoubleQuote(ApplicationService):
    def execute(self, dto: CommandDoubleQuote) -> DataTransferObject:
        return self.feature_bus.execute(CommandQuote(amount=dto.amount * 2))
"""

FEATURE_FOR_CHECKOUT = """
from sincpro_framework import Feature

from sincpro_runtime.billing.checkout import CommandCheckout


class CheckoutAsFeature(Feature):
    def execute(self, dto: CommandCheckout) -> None:
        return None
"""
