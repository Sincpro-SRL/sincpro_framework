"""Workflows: definitions as data, validated against the bus they run on, run step by step with a
trace of what ran, what was skipped and why."""

import json
from pathlib import Path

import pytest

from sincpro_framework.workflows import (
    FileWorkflows,
    InMemoryWorkflows,
    Limits,
    StepStatus,
    WorkflowFailed,
    Workflows,
)
from tests.workflows.conftest import BILL_ORDER, billing_bus


def _workflows(calls: list[str], *definitions: dict) -> Workflows:
    return Workflows(billing_bus(calls), InMemoryWorkflows(list(definitions)))


# ---------------------------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------------------------


def test_a_workflow_executes_its_commands_in_order_mapping_each_input(calls):
    workflows = _workflows(calls, BILL_ORDER)

    run = workflows.run("bill_order", {"order_id": 1})

    assert calls == ["invoice 1 15000", "approval F-1"]
    assert run.output == {"invoice_id": "F-1"}
    assert [(step.id, step.status) for step in run.steps] == [
        ("order", StepStatus.RAN),
        ("invoice", StepStatus.RAN),
        ("approval", StepStatus.RAN),
    ]


def test_a_step_whose_condition_does_not_hold_is_skipped_and_says_so(calls):
    workflows = _workflows(calls, BILL_ORDER)

    run = workflows.run("bill_order", {"order_id": 2})

    assert calls == ["invoice 2 300"]
    approval = run.steps[-1]
    assert approval.status == StepStatus.SKIPPED
    assert "$steps.order.total" in approval.reason


def test_a_code_step_computes_from_the_input_and_earlier_steps(calls):
    discounted = {
        "name": "discounted",
        "input": {"order_id": "integer"},
        "steps": [
            {
                "id": "order",
                "execute": "CommandGetOrder",
                "input": {"order_id": "$input.order_id"},
            },
            {
                "id": "discount",
                "code": "total = steps['order']['total']\nreturn {'total': total * 9 // 10}",
                "returns": ["total"],
            },
            {
                "id": "invoice",
                "execute": "CommandCreateInvoice",
                "input": {"order_id": "$input.order_id", "total": "$steps.discount.total"},
            },
        ],
    }
    workflows = _workflows(calls, discounted)

    workflows.run("discounted", {"order_id": 1})

    assert calls == ["invoice 1 13500"]


def test_for_each_runs_its_steps_once_per_item(calls):
    reserve = {
        "name": "reserve_lines",
        "input": {"order_id": "integer"},
        "steps": [
            {
                "id": "order",
                "execute": "CommandGetOrder",
                "input": {"order_id": "$input.order_id"},
            },
            {
                "id": "lines",
                "for_each": "$steps.order.lines",
                "steps": [
                    {
                        "id": "reserve",
                        "execute": "CommandReserve",
                        "input": {"product": "$item.product", "quantity": "$item.quantity"},
                    }
                ],
            },
        ],
        "output": {"lines": "$steps.lines.items"},
    }
    workflows = _workflows(calls, reserve)

    run = workflows.run("reserve_lines", {"order_id": 1})

    assert calls == ["reserve chair 2", "reserve desk 1"]
    assert run.output == {"lines": [{"reserve": {"reserved": True}}] * 2}


def test_fail_stops_the_run_with_its_message_and_nothing_after_it_runs(calls):
    guarded = {
        "name": "guarded",
        "input": {"order_id": "integer"},
        "steps": [
            {
                "id": "order",
                "execute": "CommandGetOrder",
                "input": {"order_id": "$input.order_id"},
            },
            {
                "id": "too_big",
                "fail": "orders over 10000 need a manager",
                "when": {"field": "$steps.order.total", "operator": ">", "value": 10_000},
            },
            {
                "id": "invoice",
                "execute": "CommandCreateInvoice",
                "input": {"order_id": "$input.order_id", "total": "$steps.order.total"},
            },
        ],
    }
    workflows = _workflows(calls, guarded)

    with pytest.raises(WorkflowFailed, match="orders over 10000 need a manager") as failure:
        workflows.run("guarded", {"order_id": 1})

    assert calls == []
    assert [step.status for step in failure.value.run.steps] == [
        StepStatus.RAN,
        StepStatus.FAILED,
    ]


def test_a_failing_command_fails_the_run_naming_the_step_with_the_trace_attached(calls):
    wrong = {
        "name": "wrong",
        "steps": [
            {"id": "order", "execute": "CommandGetOrder", "input": {"order_id": "abc"}}
        ],
    }
    workflows = _workflows(calls, wrong)

    with pytest.raises(WorkflowFailed, match="wrong.*order") as failure:
        workflows.run("wrong", {})

    assert failure.value.run.steps[0].status == StepStatus.FAILED


def test_the_input_of_a_run_is_checked_against_what_the_workflow_declares(calls):
    workflows = _workflows(calls, BILL_ORDER)

    with pytest.raises(WorkflowFailed, match="order_id"):
        workflows.run("bill_order", {})


def test_a_run_that_takes_more_steps_than_allowed_is_stopped(calls):
    many = {
        "name": "many",
        "input": {"order_id": "integer"},
        "steps": [
            {
                "id": "order",
                "execute": "CommandGetOrder",
                "input": {"order_id": "$input.order_id"},
            },
            {
                "id": "lines",
                "for_each": "$steps.order.lines",
                "steps": [{"id": "noop", "code": "return {}", "returns": []}],
            },
        ],
    }
    workflows = Workflows(billing_bus(calls), InMemoryWorkflows([many]), Limits(max_steps=2))

    with pytest.raises(WorkflowFailed, match="more than 2 steps"):
        workflows.run("many", {"order_id": 1})


def test_a_snippet_that_raises_shows_its_own_line_in_the_traceback(calls):
    broken = {
        "name": "broken",
        "steps": [{"id": "boom", "code": "x = 1\nreturn {'y': 1 // 0}", "returns": ["y"]}],
    }
    workflows = _workflows(calls, broken)

    with pytest.raises(WorkflowFailed) as failure:
        workflows.run("broken", {})

    step = failure.value.run.steps[0]
    assert "snippet://broken/boom" in step.error and "line 2" in step.error
    assert "1 // 0" in step.error


def test_a_dry_run_answers_commands_from_the_responses_given_and_runs_nothing(calls):
    workflows = _workflows(calls, BILL_ORDER)

    run = workflows.dry_run(
        "bill_order",
        {"order_id": 7},
        responses={
            "CommandGetOrder": {"order_id": 7, "total": 50, "lines": []},
            "CommandCreateInvoice": {"invoice_id": "DRY-7", "total": 50},
        },
    )

    assert calls == []
    assert run.output == {"invoice_id": "DRY-7"}


def test_a_dry_run_without_a_response_for_a_command_fails_naming_it(calls):
    workflows = _workflows(calls, BILL_ORDER)

    with pytest.raises(WorkflowFailed, match="CommandGetOrder.*no response"):
        workflows.dry_run("bill_order", {"order_id": 7}, responses={})


# ---------------------------------------------------------------------------------------------
# Loading, versions, reload
# ---------------------------------------------------------------------------------------------


def test_workflows_are_loaded_from_json_files(calls, folder: Path):
    folder.mkdir()
    (folder / "bill_order.json").write_text(json.dumps(BILL_ORDER))
    workflows = Workflows(billing_bus(calls), FileWorkflows(folder))

    run = workflows.run("bill_order", {"order_id": 1})

    assert run.output == {"invoice_id": "F-1"} and run.version == workflows.current.version


def test_an_invalid_set_never_replaces_the_valid_one_in_force(calls, folder: Path):
    folder.mkdir()
    (folder / "bill_order.json").write_text(json.dumps(BILL_ORDER))
    workflows = Workflows(billing_bus(calls), FileWorkflows(folder))
    assert workflows.reload() == []
    good = workflows.current.version

    broken = dict(BILL_ORDER, steps=[{"id": "x", "execute": "CommandThatDoesNotExist"}])
    (folder / "bill_order.json").write_text(json.dumps(broken))
    issues = workflows.reload()

    assert issues and "CommandThatDoesNotExist" in issues[0].message
    assert workflows.current.version == good
    assert workflows.run("bill_order", {"order_id": 1}).output == {"invoice_id": "F-1"}


def test_refresh_reloads_only_when_the_source_changed(calls, folder: Path):
    folder.mkdir()
    (folder / "bill_order.json").write_text(json.dumps(BILL_ORDER))
    workflows = Workflows(billing_bus(calls), FileWorkflows(folder))

    assert workflows.refresh() is True
    assert workflows.refresh() is False
    (folder / "bill_order.json").write_text(json.dumps(dict(BILL_ORDER, output={})))
    assert workflows.refresh() is True


# ---------------------------------------------------------------------------------------------
# On the bus
# ---------------------------------------------------------------------------------------------


def test_a_workflow_runs_as_a_command_on_the_bus(calls):
    from sincpro_framework.workflows import CommandRunWorkflow, ResponseRunWorkflow

    billing = billing_bus(calls)
    workflows = Workflows(billing, InMemoryWorkflows([BILL_ORDER]))
    workflows.expose()

    answer = billing(
        CommandRunWorkflow(workflow="bill_order", input={"order_id": 1}), ResponseRunWorkflow
    )

    assert answer is not None and answer.output == {"invoice_id": "F-1"}


def test_a_veto_records_its_message_as_the_reason_not_a_traceback(calls):
    vetoed = {"name": "vetoed", "steps": [{"id": "stop", "fail": "not today"}]}
    workflows = _workflows(calls, vetoed)

    with pytest.raises(WorkflowFailed) as failure:
        workflows.run("vetoed", {})

    step = failure.value.run.steps[0]
    assert step.reason == "not today" and "Traceback" not in step.error


def test_a_dry_run_answers_each_call_of_a_command_from_its_list_of_responses(calls):
    reserve = {
        "name": "reserve_dry",
        "input": {"order_id": "integer"},
        "steps": [
            {
                "id": "order",
                "execute": "CommandGetOrder",
                "input": {"order_id": "$input.order_id"},
            },
            {
                "id": "lines",
                "for_each": "$steps.order.lines",
                "steps": [
                    {
                        "id": "reserve",
                        "execute": "CommandReserve",
                        "input": {"product": "$item.product", "quantity": "$item.quantity"},
                    }
                ],
            },
        ],
        "output": {"lines": "$steps.lines.items"},
    }
    workflows = _workflows(calls, reserve)

    run = workflows.dry_run(
        "reserve_dry",
        {"order_id": 1},
        responses={
            "CommandGetOrder": {
                "order_id": 1,
                "total": 1,
                "lines": [{"product": "a", "quantity": 1}, {"product": "b", "quantity": 2}],
            },
            "CommandReserve": [{"reserved": True}, {"reserved": False}],
        },
    )

    assert run.output == {
        "lines": [{"reserve": {"reserved": True}}, {"reserve": {"reserved": False}}]
    }


def test_a_draft_is_rehearsed_before_it_is_stored(calls):
    workflows = _workflows(calls)
    draft = {
        "name": "draft",
        "steps": [{"id": "order", "execute": "CommandGetOrder", "input": {"order_id": 3}}],
    }

    run = workflows.dry_run(
        draft, {}, responses={"CommandGetOrder": {"order_id": 3, "total": 1, "lines": []}}
    )

    assert run.steps[0].output == {"order_id": 3, "total": 1, "lines": []}


def test_a_skipped_step_whose_condition_short_circuits_past_an_unreachable_field(calls):
    guarded = {
        "name": "guarded_skip",
        "input": {"order_id": "integer"},
        "steps": [
            {
                "id": "order",
                "execute": "CommandGetOrder",
                "input": {"order_id": "$input.order_id"},
            },
            {
                "id": "maybe",
                "execute": "CommandReserve",
                "input": {"product": "x", "quantity": 1},
                "when": {"field": "$steps.order.total", "operator": ">", "value": 10**9},
            },
            {
                "id": "after",
                "fail": "never",
                "when": {
                    "all": [
                        {"field": "$steps.order.total", "operator": ">", "value": 10**9},
                        {"field": "$steps.maybe.reserved", "operator": "=", "value": True},
                    ]
                },
            },
        ],
    }
    workflows = _workflows(calls, guarded)

    run = workflows.run("guarded_skip", {"order_id": 1})

    assert [step.status for step in run.steps] == [
        StepStatus.RAN,
        StepStatus.SKIPPED,
        StepStatus.SKIPPED,
    ]
    assert "$steps.maybe.reserved is not reached" in run.steps[-1].reason


def test_a_skipped_step_inside_for_each_is_left_out_of_its_items(calls):
    loop = {
        "name": "loop_skip",
        "input": {"order_id": "integer"},
        "steps": [
            {
                "id": "order",
                "execute": "CommandGetOrder",
                "input": {"order_id": "$input.order_id"},
            },
            {
                "id": "lines",
                "for_each": "$steps.order.lines",
                "steps": [
                    {
                        "id": "big",
                        "execute": "CommandReserve",
                        "input": {"product": "$item.product", "quantity": "$item.quantity"},
                        "when": {"field": "$item.quantity", "operator": ">", "value": 1},
                    }
                ],
            },
        ],
        "output": {"items": "$steps.lines.items"},
    }
    workflows = _workflows(calls, loop)

    run = workflows.run("loop_skip", {"order_id": 1})

    assert run.output == {"items": [{"big": {"reserved": True}}, {}]}


def test_a_for_each_over_more_items_than_allowed_is_stopped(calls):
    loop = {
        "name": "too_many",
        "input": {"order_id": "integer"},
        "steps": [
            {
                "id": "order",
                "execute": "CommandGetOrder",
                "input": {"order_id": "$input.order_id"},
            },
            {
                "id": "lines",
                "for_each": "$steps.order.lines",
                "steps": [{"id": "n", "code": "return {}", "returns": []}],
            },
        ],
    }
    workflows = Workflows(billing_bus(calls), InMemoryWorkflows([loop]), Limits(max_items=1))

    with pytest.raises(WorkflowFailed, match="2 items, more than 1"):
        workflows.run("too_many", {"order_id": 1})


def test_a_workflow_that_runs_itself_stops_at_the_depth_allowed(calls):
    from sincpro_framework.workflows import CommandRunWorkflow  # noqa: F401

    again = {
        "name": "again",
        "steps": [
            {"id": "again", "execute": "CommandRunWorkflow", "input": {"workflow": "again"}}
        ],
    }
    billing = billing_bus(calls)
    workflows = Workflows(billing, InMemoryWorkflows([again]), Limits(max_depth=3))
    workflows.expose()

    with pytest.raises(WorkflowFailed, match="nested more than 3 deep"):
        workflows.run("again", {})


def test_a_workflow_executing_one_the_set_does_not_have_is_refused(calls):
    billing = billing_bus(calls)
    caller = {
        "name": "caller",
        "steps": [
            {"id": "x", "execute": "CommandRunWorkflow", "input": {"workflow": "nope"}}
        ],
    }
    workflows = Workflows(billing, InMemoryWorkflows([caller]))
    workflows.expose()

    issues = workflows.reload()

    assert any("nope" in issue.message for issue in issues)


def test_expose_after_the_bus_was_built_says_why(calls):
    billing = billing_bus(calls)
    workflows = Workflows(billing, InMemoryWorkflows([BILL_ORDER]))
    workflows.catalog()

    with pytest.raises(RuntimeError, match="expose.*before.*built"):
        workflows.expose()


def test_an_empty_or_missing_folder_never_replaces_the_set_in_force(calls, folder: Path):
    folder.mkdir()
    (folder / "bill_order.json").write_text(json.dumps(BILL_ORDER))
    workflows = Workflows(billing_bus(calls), FileWorkflows(folder))
    assert workflows.reload() == []

    (folder / "bill_order.json").unlink()
    issues = workflows.reload()

    assert issues and "no workflow" in issues[0].message
    assert "bill_order" in workflows.current.workflows
    assert Workflows(billing_bus(calls), FileWorkflows(folder / "nope")).reload()


def test_a_file_that_cannot_be_read_as_text_is_an_issue(calls, folder: Path):
    folder.mkdir()
    (folder / "broken.json").write_bytes(b"\xff\xfe\x00garbage")

    issues = Workflows(billing_bus(calls), FileWorkflows(folder)).reload()

    assert issues and "broken.json" in str(issues[0])
