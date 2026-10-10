"""What an agent needs to write a definition that runs the first time: the JSON Schema of a
workflow, the catalog of what the bus can execute with each input and response schema, and a
drawing of what a workflow does."""

from sincpro_framework.runtime.workflows import InMemoryWorkflows, Workflows
from tests.runtime.workflows.conftest import BILL_ORDER, billing_bus


def test_the_schema_of_a_workflow_is_published():
    schema = Workflows.schema()

    step = schema["$defs"]["Step"]["properties"]
    assert {"execute", "code", "for_each", "fail", "when", "input", "returns"} <= set(step)


def test_the_catalog_lists_every_command_with_its_input_and_response_schemas():
    workflows = Workflows(billing_bus([]), InMemoryWorkflows([]))

    catalog = workflows.catalog()

    order = catalog["CommandGetOrder"]
    assert order["input"]["properties"]["order_id"]["type"] == "integer"
    assert "total" in order["response"]["properties"]
    assert catalog["CommandRequestApproval"]["response"] is None


def test_a_workflow_is_drawn_as_a_mermaid_flowchart():
    workflows = Workflows(billing_bus([]), InMemoryWorkflows([BILL_ORDER]))

    drawing = workflows.draw("bill_order")

    assert drawing.startswith("flowchart TD")
    assert "order --> invoice" in drawing
    assert "approval" in drawing and "when" in drawing
