"""Validation: every problem of a definition at once, each naming the workflow, the step and the
path, in words an agent can act on — checked against the live bus, never left to fail at run time.
"""

from sincpro_framework.runtime.workflows import InMemoryWorkflows, Workflows
from tests.runtime.workflows.conftest import BILL_ORDER, billing_bus


def _issues(definition: dict) -> list[str]:
    workflows = Workflows(billing_bus([]), InMemoryWorkflows([]))
    return [
        f"{issue.step}:{issue.path}: {issue.message}"
        for issue in workflows.validate(definition)
    ]


def _with_steps(*steps: dict, **fields) -> dict:
    return {"name": "w", "input": {"order_id": "integer"}, "steps": list(steps), **fields}


def test_a_valid_definition_has_no_issue():
    assert _issues(BILL_ORDER) == []


def test_a_command_the_bus_does_not_have_is_named_with_what_it_has():
    issues = _issues(_with_steps({"id": "x", "execute": "CommandNope", "input": {}}))

    assert len(issues) == 1 and "CommandNope" in issues[0] and "CommandGetOrder" in issues[0]


def test_an_input_the_command_does_not_declare_and_a_required_one_missing():
    issues = _issues(
        _with_steps({"id": "x", "execute": "CommandCreateInvoice", "input": {"order": 1}})
    )

    assert any("x:input.order" in one and "not a field" in one for one in issues)
    assert any("x:input" in one and "order_id" in one and "required" in one for one in issues)


def test_a_reference_to_a_later_step_or_an_unknown_field_is_refused():
    issues = _issues(
        _with_steps(
            {
                "id": "invoice",
                "execute": "CommandCreateInvoice",
                "input": {"order_id": "$steps.order.order_id", "total": "$input.amount"},
            },
            {
                "id": "order",
                "execute": "CommandGetOrder",
                "input": {"order_id": "$input.order_id"},
            },
        )
    )

    assert any("$steps.order" in one and "before" in one for one in issues)
    assert any("$input.amount" in one and "order_id" in one for one in issues)


def test_a_reference_to_a_field_the_step_does_not_answer_is_refused():
    issues = _issues(
        _with_steps(
            {
                "id": "order",
                "execute": "CommandGetOrder",
                "input": {"order_id": "$input.order_id"},
            },
            {
                "id": "invoice",
                "execute": "CommandCreateInvoice",
                "input": {"order_id": "$input.order_id", "total": "$steps.order.amount"},
            },
        )
    )

    assert any("$steps.order.amount" in one and "total" in one for one in issues)


def test_a_snippet_is_checked_for_syntax_return_and_names_it_does_not_have():
    issues = _issues(
        _with_steps(
            {"id": "bad_syntax", "code": "return {", "returns": []},
            {"id": "no_return", "code": "x = 1", "returns": []},
            {"id": "unknown_name", "code": "return {'x': orders}", "returns": ["x"]},
        )
    )

    assert any(one.startswith("bad_syntax:code") and "line 1" in one for one in issues)
    assert any(one.startswith("no_return:code") and "return" in one for one in issues)
    assert any(one.startswith("unknown_name:code") and "orders" in one for one in issues)


def test_a_step_needs_exactly_one_kind_and_ids_are_unique_identifiers():
    issues = _issues(
        _with_steps(
            {"id": "a", "execute": "CommandGetOrder", "code": "return {}", "input": {}},
            {"id": "b"},
            {"id": "not an id", "fail": "x"},
            {"id": "c", "fail": "x"},
            {"id": "c", "fail": "y"},
        )
    )

    assert any(one.startswith("a:") and "one of" in one for one in issues)
    assert any(one.startswith("b:") and "one of" in one for one in issues)
    assert any("not an id" in one and "identifier" in one for one in issues)
    assert any(one.startswith("c:") and "twice" in one for one in issues)


def test_a_condition_on_a_reference_that_does_not_exist_is_refused():
    issues = _issues(
        _with_steps(
            {
                "id": "x",
                "fail": "no",
                "when": {"field": "$steps.ghost.total", "operator": ">", "value": 1},
            }
        )
    )

    assert any("$steps.ghost" in one for one in issues)


def test_every_issue_comes_back_at_once():
    issues = _issues(
        _with_steps(
            {"id": "a", "execute": "CommandNope", "input": {}},
            {"id": "b", "code": "x = 1", "returns": []},
            {
                "id": "c",
                "fail": "x",
                "when": {"field": "$input.nope", "operator": "=", "value": 1},
            },
        )
    )

    assert len(issues) >= 3


def test_what_is_not_a_workflow_at_all_is_one_issue_saying_what_is_wrong():
    issues = _issues({"steps": "nope"})

    assert issues and all(":" in one for one in issues)


def test_a_condition_in_a_grammar_criteria_does_not_speak_is_an_issue_not_a_crash():
    issues = _issues(
        _with_steps(
            {
                "id": "x",
                "fail": "no",
                "when": {"and": [{"field": "$input.order_id", "operator": "=", "value": 1}]},
            }
        )
    )

    assert any(one.startswith("x:when") and "all" in one for one in issues)


def test_a_wrong_input_type_does_not_hide_the_issues_of_the_steps():
    issues = _issues(
        {
            "name": "w",
            "input": {"order_id": "int"},
            "steps": [{"id": "a", "execute": "CommandNope", "input": {}}],
        }
    )

    assert any("input.order_id" in one for one in issues)
    assert any("CommandNope" in one for one in issues)


def test_the_value_of_a_condition_is_a_literal_never_a_reference():
    issues = _issues(
        _with_steps(
            {
                "id": "x",
                "fail": "no",
                "when": {
                    "field": "$input.order_id",
                    "operator": "=",
                    "value": "$input.order_id",
                },
            }
        )
    )

    assert any(one.startswith("x:when") and "literal" in one for one in issues)


def test_a_field_on_a_step_of_another_kind_is_refused():
    issues = _issues(
        _with_steps(
            {"id": "a", "fail": "no", "input": {"x": 1}},
            {
                "id": "b",
                "execute": "CommandGetOrder",
                "input": {"order_id": 1},
                "returns": ["x"],
            },
        )
    )

    assert any(one.startswith("a:input") and "execute" in one for one in issues)
    assert any(one.startswith("b:returns") and "code" in one for one in issues)


def test_a_node_that_mixes_all_and_any_is_refused_not_half_read():
    issues = _issues(
        _with_steps(
            {
                "id": "x",
                "fail": "no",
                "when": {
                    "all": [],
                    "any": [{"field": "$steps.ghost.total", "operator": ">", "value": 1}],
                },
            }
        )
    )

    assert any("has no 'all'" in one for one in issues)


def test_a_snippet_whose_return_is_only_inside_a_nested_function_never_returns():
    issues = _issues(
        _with_steps(
            {"id": "x", "code": "def inner():\n    return {}\ninner()", "returns": []}
        )
    )

    assert any(one.startswith("x:code") and "return" in one for one in issues)


def test_a_snippet_that_yields_is_refused():
    issues = _issues(_with_steps({"id": "x", "code": "yield 1\nreturn {}", "returns": []}))

    assert any(one.startswith("x:code") and "yield" in one for one in issues)


def test_names_a_match_captures_are_names_the_snippet_has():
    code = "match input:\n    case {'order_id': found}:\n        return {'id': found}\nreturn {'id': 0}"

    assert _issues(_with_steps({"id": "x", "code": code, "returns": ["id"]})) == []
