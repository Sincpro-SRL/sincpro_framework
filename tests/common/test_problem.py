"""One problem per failure — its kind, what the caller reads, and what to do next — with defaults by
kind that an error changes by declaring, never by editing a table."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from sincpro_framework import DataTransferObject, exceptions
from sincpro_framework.auth.domain.exceptions import PermissionDenied, Unauthenticated
from sincpro_framework.common.failures import NEXT_STEP_OF, NextStep, Problem, problem_of
from sincpro_framework.ddd import exceptions as ddd_exceptions


class InvoiceNotFound(exceptions.ClientError):
    failure_kind = exceptions.FailureKind.NOT_FOUND


class InvoiceNumberMistyped(exceptions.ClientError):
    failure_kind = exceptions.FailureKind.NOT_FOUND
    next_step = NextStep.FIX_REQUEST
    guidance = "Check the invoice number on the printed copy."


class TaxAuthorityDown(exceptions.ServiceUnavailableError):
    retry_after = 30


class OverCreditLimit(ddd_exceptions.DomainError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"the customer's credit limit is {limit}")
        self.limit = limit

    def refine_problem(self, problem: Problem) -> Problem:
        return problem.model_copy(
            update={"title": "Over the credit limit", "extensions": {"limit": self.limit}}
        )


class CommandIssueInvoice(DataTransferObject):
    total: int


def test_a_failure_carries_its_kind_and_what_to_do_next():
    problem = problem_of(InvoiceNotFound("invoice F-9 does not exist"))

    assert (problem.kind, problem.reason, problem.status, problem.detail) == (
        exceptions.FailureKind.NOT_FOUND,
        "INVOICE_NOT_FOUND",
        404,
        "invoice F-9 does not exist",
    )
    assert (problem.next_step, problem.guidance, problem.retryable) == (
        NextStep.DO_NOT_RETRY,
        NEXT_STEP_OF[exceptions.FailureKind.NOT_FOUND][1],
        False,
    )


def test_every_kind_has_a_next_step_and_its_guidance():
    assert set(NEXT_STEP_OF) == set(exceptions.FailureKind)
    assert all(guidance for _, guidance in NEXT_STEP_OF.values())


def test_a_crash_tells_nothing_of_the_inside_and_says_to_try_later():
    problem = problem_of(RuntimeError("postgres://admin:hunter2@db"))

    assert (problem.kind, problem.reason, problem.detail) == (
        exceptions.FailureKind.INTERNAL,
        "INTERNAL",
        None,
    )
    assert problem.next_step is NextStep.RETRY_LATER
    assert "hunter2" not in problem.model_dump_json()


def test_a_service_down_says_how_long_to_wait_in_seconds():
    problem = problem_of(TaxAuthorityDown("SIAT did not answer"))

    assert (problem.next_step, problem.retryable, problem.retry_after) == (
        NextStep.RETRY_LATER,
        True,
        timedelta(seconds=30),
    )
    assert problem.model_dump(mode="json")["retry_after"] == 30.0
    assert problem.detail is None  # a service down is the inside: nothing of it is told


def test_a_request_that_does_not_validate_points_at_each_field():
    with pytest.raises(ValidationError) as refused:
        CommandIssueInvoice.model_validate({"total": "ten"})

    problem = problem_of(refused.value)
    assert (problem.kind, problem.next_step) == (
        exceptions.FailureKind.INVALID,
        NextStep.FIX_REQUEST,
    )
    assert [one.pointer for one in problem.errors] == ["#/total"]


def test_an_error_declares_its_own_next_step_and_guidance():
    problem = problem_of(InvoiceNumberMistyped("no invoice F-9O"))

    assert (problem.kind, problem.next_step, problem.guidance) == (
        exceptions.FailureKind.NOT_FOUND,
        NextStep.FIX_REQUEST,
        "Check the invoice number on the printed copy.",
    )


def test_an_error_refines_anything_else_of_its_problem():
    problem = problem_of(OverCreditLimit(5000))

    assert (problem.title, problem.extensions, problem.detail) == (
        "Over the credit limit",
        {"limit": 5000},
        "the customer's credit limit is 5000",
    )


def test_a_duplicate_says_not_to_write_it_again_unlike_a_stale_write():
    duplicate = problem_of(ddd_exceptions.DuplicateAggregate("invoice F-1 already exists"))
    stale = problem_of(ddd_exceptions.StaleAggregate("invoice F-1 was written by someone"))

    assert (duplicate.next_step, duplicate.retryable) == (NextStep.DO_NOT_RETRY, False)
    assert (stale.next_step, stale.retryable) == (NextStep.REFRESH_AND_RETRY, True)


def test_an_auth_refusal_says_why_and_what_is_missing():
    denied = problem_of(PermissionDenied("user:1", "billing.invoice.issue"))
    stepped = problem_of(Unauthenticated("mfa required", step_up="mfa"))

    assert (denied.status, denied.detail, denied.extensions) == (
        403,
        "lacks billing.invoice.issue",
        {"requirement": "billing.invoice.issue"},
    )
    assert (stepped.next_step, stepped.extensions) == (
        NextStep.AUTHENTICATE,
        {"step_up": "mfa"},
    )


def test_a_failure_no_error_raised_takes_its_kind_s_defaults():
    problem = Problem.for_kind(exceptions.FailureKind.INVALID, "UNREADABLE", "not JSON")

    assert (problem.status, problem.reason, problem.next_step, problem.detail) == (
        422,
        "UNREADABLE",
        NextStep.FIX_REQUEST,
        "not JSON",
    )
