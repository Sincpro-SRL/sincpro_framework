"""Approvals: a use case runs only once a person approved what it would do — then only the input
they approved, once; a decision that came late, or from someone else, runs nothing."""

import threading
import time
from datetime import timedelta
from typing import Any

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.approvals import (
    Approval,
    ApprovalClosed,
    ApprovalNotFound,
    ApprovalRecord,
    ApprovalRequests,
    ApprovalRequired,
    Approvals,
    ApprovalState,
    Approve,
    FindApprovals,
    GetApproval,
    Preview,
    Reject,
    ResponseApprovals,
)
from sincpro_framework.context import InMemoryContexts, KeyValueContexts
from sincpro_framework.data_layer.caching import IdempotencyOutcome
from sincpro_framework.data_layer.store import InMemoryKeyValue, StoreUnavailable
from sincpro_framework.entrypoints.services.scalar_execution import ScalarExecution
from sincpro_framework.exceptions import FailureKind, ProgrammingError

ANA = {"tenant_id": "acme", "user_id": "ana"}
EVE = {"tenant_id": "acme", "user_id": "eve"}


class CommandCreateQuotation(DataTransferObject):
    customer: str
    total: int


class ResponseQuotation(DataTransferObject):
    quotation_id: str
    total: int


class CommandCreateContact(DataTransferObject):
    name: str


class ResponseContact(DataTransferObject):
    contact_id: str


class CommandSell(DataTransferObject):
    customer: str
    total: int


class ResponseSold(DataTransferObject):
    contact_id: str
    quotation_id: str


def sales_bus(
    written: list[str],
    fails: list[Exception] | None = None,
    store: Any = None,
    replayed_for: timedelta = timedelta(days=1),
) -> UseFramework:
    """A bus whose quotations and contacts need approval, and a sale that asks for one approval
    of both — `written` is what reached the customer's system."""
    pending = list(fails or [])
    bus = UseFramework("sales-approvals", log_after_execution=False)
    bus.store_context_in(store or InMemoryContexts())

    @bus.feature(CommandCreateQuotation)
    class CreateQuotation(Feature):
        approval = Approval()

        def preview(self, dto: CommandCreateQuotation) -> str:
            if dto.total <= 0:
                raise ValueError("a quotation for nothing")
            return f"Quotation for {dto.customer}, Bs {dto.total}"

        def execute(self, dto: CommandCreateQuotation) -> ResponseQuotation:
            if pending:
                raise pending.pop(0)
            written.append(f"quotation {dto.customer} {dto.total}")
            return ResponseQuotation(quotation_id=f"Q-{len(written)}", total=dto.total)

    @bus.feature(CommandCreateContact)
    class CreateContact(Feature):
        approval = Approval()

        def preview(self, dto: CommandCreateContact) -> Preview:
            return Preview(text=f"Contact {dto.name}", data={"name": dto.name})

        def execute(self, dto: CommandCreateContact) -> ResponseContact:
            written.append(f"contact {dto.name}")
            return ResponseContact(contact_id=f"C-{dto.name}")

    @bus.app_service(CommandSell)
    class Sell(ApplicationService):
        approval = Approval()

        def preview(self, dto: CommandSell) -> str:
            contact = self.feature_bus(
                CommandCreateContact(name=dto.customer), ApprovalRequired
            )
            quotation = self.feature_bus(
                CommandCreateQuotation(customer=dto.customer, total=dto.total),
                ApprovalRequired,
            )
            return f"{contact.preview.text}; then {quotation.preview.text}"

        def execute(self, dto: CommandSell) -> ResponseSold:
            contact = self.feature_bus(
                CommandCreateContact(name=dto.customer), ResponseContact
            )
            quotation = self.feature_bus(
                CommandCreateQuotation(customer=dto.customer, total=dto.total),
                ResponseQuotation,
            )
            return ResponseSold(
                contact_id=contact.contact_id, quotation_id=quotation.quotation_id
            )

    @bus.feature([Approve, Reject, GetApproval, FindApprovals])
    class SalesApprovals(Approvals): ...

    SalesApprovals.replayed_for = replayed_for

    return bus


def asked(bus: UseFramework, command: Any, who: dict[str, str] = ANA) -> ApprovalRequired:
    with bus.context(who):
        answer = bus(command)
    assert isinstance(answer, ApprovalRequired)
    return answer


def test_a_use_case_that_needs_approval_answers_what_it_would_do_and_runs_nothing():
    written: list[str] = []
    bus = sales_bus(written)

    answer = asked(bus, CommandCreateQuotation(customer="Juan", total=1150))

    assert written == []
    assert answer.status == "approval_required" and len(answer.approval_id) >= 30
    assert answer.preview.text == "Quotation for Juan, Bs 1150"
    assert answer.use_case == "sales-approvals.CommandCreateQuotation"
    assert answer.expires_at is not None


def test_approving_runs_the_input_that_was_asked_never_another():
    written: list[str] = []
    bus = sales_bus(written)
    first = asked(bus, CommandCreateQuotation(customer="Juan", total=1150))
    asked(bus, CommandCreateQuotation(customer="Juan", total=9999))  # asked again, otherwise

    with bus.context(ANA):
        done = bus(Approve(approval_id=first.approval_id), ResponseQuotation)

    assert written == ["quotation Juan 1150"] and done.total == 1150
    with bus.context(ANA):
        record = bus(GetApproval(approval_id=first.approval_id), ApprovalRecord)
    assert record.state is ApprovalState.APPROVED and record.decided_at is not None


def test_a_second_approval_answers_what_the_first_ran_and_says_it_was_a_replay():
    written: list[str] = []
    bus = sales_bus(written)
    pending = asked(bus, CommandCreateQuotation(customer="Juan", total=1150))

    with bus.context(ANA):
        first = bus(Approve(approval_id=pending.approval_id))
        with bus.idempotency.outcomes() as seen:
            again = bus(Approve(approval_id=pending.approval_id))

    assert written == ["quotation Juan 1150"] and again == first
    assert seen == [IdempotencyOutcome.REPLAYED]


def test_an_approval_that_ran_never_runs_again_once_its_answer_is_no_longer_kept():
    written: list[str] = []
    bus = sales_bus(written, replayed_for=timedelta(milliseconds=5))
    pending = asked(bus, CommandCreateQuotation(customer="Juan", total=1150))

    with bus.context(ANA):
        bus(Approve(approval_id=pending.approval_id))
        time.sleep(0.02)  # past replayed_for: the answer is no longer kept
        with pytest.raises(ApprovalClosed, match="never runs again"):
            bus(Approve(approval_id=pending.approval_id))
        answered = bus(GetApproval(approval_id=pending.approval_id), ApprovalRecord)

    assert answered.answered_at is not None and written == ["quotation Juan 1150"]


def test_two_approvals_at_once_run_it_once_and_both_get_its_answer():
    written: list[str] = []
    bus = sales_bus(written)
    pending = asked(bus, CommandCreateQuotation(customer="Juan", total=1150))
    answers: list[Any] = []
    start = threading.Barrier(2)

    def approve() -> None:
        start.wait()
        with bus.context(ANA):
            answers.append(bus(Approve(approval_id=pending.approval_id)))

    threads = [threading.Thread(target=approve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert written == ["quotation Juan 1150"]
    assert len(answers) == 2 and answers[0] == answers[1]


def test_nothing_runs_without_an_approval_of_its_own():
    written: list[str] = []
    bus = sales_bus(written)

    with bus.context(ANA), pytest.raises(ApprovalNotFound) as raised:
        bus(Approve(approval_id="never-asked"))

    assert raised.value.failure_kind is FailureKind.NOT_FOUND and written == []


def test_another_person_of_the_same_tenant_cannot_decide_it():
    written: list[str] = []
    bus = sales_bus(written)
    pending = asked(bus, CommandCreateQuotation(customer="Juan", total=1150), ANA)

    with bus.context(EVE), pytest.raises(ApprovalNotFound):
        bus(Approve(approval_id=pending.approval_id))

    assert written == []


def test_a_rejected_approval_stays_rejected_and_is_never_approved():
    written: list[str] = []
    bus = sales_bus(written)
    pending = asked(bus, CommandCreateQuotation(customer="Juan", total=1150))

    with bus.context(ANA):
        rejected = bus(
            Reject(approval_id=pending.approval_id, reason="wrong price"), ApprovalRecord
        )
        with pytest.raises(ApprovalClosed, match="rejected \\(wrong price\\)") as raised:
            bus(Approve(approval_id=pending.approval_id))

    assert rejected.state is ApprovalState.REJECTED and rejected.reason == "wrong price"
    assert raised.value.state is ApprovalState.REJECTED and written == []


def test_an_approval_nobody_decided_in_time_expires():
    written: list[str] = []
    bus = sales_bus(written)
    pending = asked(bus, CommandCreateQuotation(customer="Juan", total=1150))
    with bus.context(ANA):
        requests = ApprovalRequests.of(bus.current_context())
        held = requests.read(pending.approval_id)
        assert held is not None
        requests.save(
            held.model_copy(update={"expires_at": held.requested_at}), held.store_version
        )

        with pytest.raises(ApprovalClosed, match="is expired") as raised:
            bus(Approve(approval_id=pending.approval_id))
        found = bus(FindApprovals(state=ApprovalState.EXPIRED), ResponseApprovals)

    assert raised.value.state is ApprovalState.EXPIRED and written == []
    assert [one.approval_id for one in found.approvals] == [pending.approval_id]


def test_an_approved_one_is_never_rejected():
    written: list[str] = []
    bus = sales_bus(written)
    pending = asked(bus, CommandCreateQuotation(customer="Juan", total=1150))

    with bus.context(ANA):
        bus(Approve(approval_id=pending.approval_id))
        with pytest.raises(ApprovalClosed, match="cannot be rejected"):
            bus(Reject(approval_id=pending.approval_id))


def test_a_run_that_fails_leaves_the_approval_pending_to_approve_again():
    written: list[str] = []
    bus = sales_bus(written, fails=[ConnectionError("odoo did not answer")])
    pending = asked(bus, CommandCreateQuotation(customer="Juan", total=1150))

    with bus.context(ANA):
        with pytest.raises(ConnectionError):
            bus(Approve(approval_id=pending.approval_id))
        state = bus(GetApproval(approval_id=pending.approval_id), ApprovalRecord).state
        done = bus(Approve(approval_id=pending.approval_id), ResponseQuotation)

    assert state is ApprovalState.PENDING
    assert done.total == 1150 and written == ["quotation Juan 1150"]


def test_the_pending_approvals_of_the_caller_are_listed():
    written: list[str] = []
    bus = sales_bus(written)
    first = asked(bus, CommandCreateQuotation(customer="Juan", total=1))
    second = asked(bus, CommandCreateContact(name="Ana"))
    asked(bus, CommandCreateContact(name="Eve"), EVE)

    with bus.context(ANA):
        found = bus(FindApprovals(), ResponseApprovals)

    assert [one.approval_id for one in found.approvals] == [
        second.approval_id,
        first.approval_id,
    ]
    assert found.approvals[0].preview.data == {"name": "Ana"}


# --- one approval for several use cases ---------------------------------------------------------


def test_an_application_service_asks_one_approval_for_everything_it_runs():
    written: list[str] = []
    bus = sales_bus(written)

    pending = asked(bus, CommandSell(customer="Juan", total=1150))
    with bus.context(ANA):
        nothing_else = bus(FindApprovals(), ResponseApprovals)
        sold = bus(Approve(approval_id=pending.approval_id), ResponseSold)

    assert pending.preview.text == "Contact Juan; then Quotation for Juan, Bs 1150"
    assert [one.approval_id for one in nothing_else.approvals] == [pending.approval_id]
    assert written == ["contact Juan", "quotation Juan 1150"]
    assert (sold.contact_id, sold.quotation_id) == ("C-Juan", "Q-2")


def test_what_any_of_them_refuses_in_the_preview_writes_nothing_and_asks_nothing():
    written: list[str] = []
    bus = sales_bus(written)

    with bus.context(ANA), pytest.raises(ValueError, match="a quotation for nothing"):
        bus(CommandSell(customer="Juan", total=0))
    with bus.context(ANA):
        found = bus(FindApprovals(), ResponseApprovals)

    assert written == [] and found.approvals == []


# --- the store, the wire, the declaration ------------------------------------------------------


class DownKeyValue(InMemoryKeyValue):
    """A store that does not answer."""

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        raise StoreUnavailable("DownKeyValue did not answer")

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        raise StoreUnavailable("DownKeyValue did not answer")


def test_while_the_store_does_not_answer_nothing_is_asked_and_nothing_runs():
    written: list[str] = []
    bus = sales_bus(written, store=KeyValueContexts(DownKeyValue()))

    with bus.context(ANA), pytest.raises(StoreUnavailable):
        bus(CommandCreateQuotation(customer="Juan", total=1150))

    assert written == []


def test_a_wire_carries_it_as_a_result_not_as_an_error():
    written: list[str] = []
    bus = sales_bus(written)

    with bus.context(ANA):
        answer = ScalarExecution(bus).runner(CommandCreateQuotation)(
            {"customer": "Juan", "total": 1150}
        )

    assert answer["status"] == "approval_required" and answer["approval_id"]
    assert answer["preview"]["text"] == "Quotation for Juan, Bs 1150"


def test_an_approval_with_nothing_to_show_cannot_be_declared():
    bus = UseFramework("sales-no-preview", log_after_execution=False)

    @bus.feature(CommandCreateContact)
    class CreateContact(Feature):
        approval = Approval()

        def execute(self, dto: CommandCreateContact) -> ResponseContact:
            return ResponseContact(contact_id="C-1")

    with pytest.raises(ProgrammingError, match="has no preview"):
        bus.build_root_bus()
