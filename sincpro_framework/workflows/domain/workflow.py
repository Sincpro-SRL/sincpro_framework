"""A workflow: a definition as data — the steps a bounded context runs, in order, on its bus.

    {"name": "bill_order",
     "input": {"order_id": "integer"},
     "steps": [
       {"id": "order",   "execute": "CommandGetOrder", "input": {"order_id": "$input.order_id"}},
       {"id": "invoice", "execute": "CommandCreateInvoice",
        "input": {"order_id": "$steps.order.order_id", "total": "$steps.order.total"}},
       {"id": "approval", "execute": "CommandRequestApproval",
        "input": {"invoice_id": "$steps.invoice.invoice_id"},
        "when": {"field": "$steps.order.total", "operator": ">=", "value": 10000}}],
     "output": {"invoice_id": "$steps.invoice.invoice_id"}}

Context: a step is one of four kinds — `execute` a Command by name, run `code` (a snippet), repeat
steps `for_each` item of a list, or `fail` with a message — and any of them may carry a `when`,
written in the grammar of `Criteria`. A value is a literal, or a reference to the whole of a
value: `$input.<field>`, `$steps.<step id>.<field>`, `$item.<field>` inside a `for_each`.
Nothing else: computing belongs in a snippet, so every reference can be checked before it runs.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from sincpro_framework.sincpro_abstractions import DataTransferObject

type InputType = Literal["string", "integer", "number", "boolean", "object", "array"]


class StepKind(StrEnum):
    EXECUTE = "execute"
    CODE = "code"
    FOR_EACH = "for_each"
    FAIL = "fail"


class Step(DataTransferObject):
    id: str
    """What later steps call it by: `$steps.<id>.<field>`. An identifier, unique in the workflow."""

    execute: str | None = None
    """A Command or Query the bus answers, by name."""

    input: dict[str, Any] = Field(default_factory=dict)
    """The fields of the Command `execute` sends — literals or references."""

    code: str | None = None
    """A snippet: Python that reads `input`, `steps` and — inside a `for_each` — `item`, and
    returns a mapping."""

    returns: list[str] = Field(default_factory=list)
    """The fields a snippet's mapping has — what later steps may reference."""

    for_each: str | None = None
    """A reference to a list: `steps` run once per item, `$item` is the item."""

    steps: list["Step"] = Field(default_factory=list)
    """The steps a `for_each` repeats."""

    fail: str | None = None
    """Stop the run with this message — a veto, a business rule that does not hold."""

    when: dict[str, Any] | None = None
    """A condition in the grammar of `Criteria` whose fields are references; the step is skipped
    when it does not hold."""

    @property
    def kinds(self) -> list[StepKind]:
        declared = {
            StepKind.EXECUTE: self.execute,
            StepKind.CODE: self.code,
            StepKind.FOR_EACH: self.for_each,
            StepKind.FAIL: self.fail,
        }
        return [kind for kind, value in declared.items() if value is not None]


class Workflow(DataTransferObject):
    name: str
    description: str = ""
    input: dict[str, InputType] = Field(default_factory=dict)
    """What a run is given — every field is required."""

    steps: list[Step]
    output: dict[str, Any] = Field(default_factory=dict)
    """What a run answers — literals or references."""
