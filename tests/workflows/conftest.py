"""A billing bus the workflows run on: an order, an invoice, an approval, and the overdue list."""

from pathlib import Path

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework


class CommandGetOrder(DataTransferObject):
    order_id: int


class Line(DataTransferObject):
    product: str
    quantity: int


class ResponseGetOrder(DataTransferObject):
    order_id: int
    total: int
    lines: list[Line]


class CommandCreateInvoice(DataTransferObject):
    order_id: int
    total: int


class ResponseCreateInvoice(DataTransferObject):
    invoice_id: str
    total: int


class CommandRequestApproval(DataTransferObject):
    invoice_id: str


class CommandReserve(DataTransferObject):
    product: str
    quantity: int


class ResponseReserve(DataTransferObject):
    reserved: bool


def billing_bus(calls: list[str]) -> UseFramework:
    billing = UseFramework("billing-workflows", log_after_execution=False)

    @billing.feature(CommandGetOrder)
    class GetOrder(Feature):
        def execute(self, dto: CommandGetOrder) -> ResponseGetOrder:
            total = 15_000 if dto.order_id == 1 else 300
            lines = [Line(product="chair", quantity=2), Line(product="desk", quantity=1)]
            return ResponseGetOrder(order_id=dto.order_id, total=total, lines=lines)

    @billing.feature(CommandCreateInvoice)
    class CreateInvoice(Feature):
        def execute(self, dto: CommandCreateInvoice) -> ResponseCreateInvoice:
            calls.append(f"invoice {dto.order_id} {dto.total}")
            return ResponseCreateInvoice(invoice_id=f"F-{dto.order_id}", total=dto.total)

    @billing.feature(CommandRequestApproval)
    class RequestApproval(Feature):
        def execute(self, dto: CommandRequestApproval) -> None:
            calls.append(f"approval {dto.invoice_id}")

    @billing.feature(CommandReserve)
    class Reserve(Feature):
        def execute(self, dto: CommandReserve) -> ResponseReserve:
            calls.append(f"reserve {dto.product} {dto.quantity}")
            return ResponseReserve(reserved=True)

    return billing


BILL_ORDER = {
    "name": "bill_order",
    "input": {"order_id": "integer"},
    "steps": [
        {
            "id": "order",
            "execute": "CommandGetOrder",
            "input": {"order_id": "$input.order_id"},
        },
        {
            "id": "invoice",
            "execute": "CommandCreateInvoice",
            "input": {"order_id": "$steps.order.order_id", "total": "$steps.order.total"},
        },
        {
            "id": "approval",
            "execute": "CommandRequestApproval",
            "input": {"invoice_id": "$steps.invoice.invoice_id"},
            "when": {"field": "$steps.order.total", "operator": ">=", "value": 10_000},
        },
    ],
    "output": {"invoice_id": "$steps.invoice.invoice_id"},
}


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    return tmp_path / "workflows"
