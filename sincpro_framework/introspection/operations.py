"""What a bus can execute, with the schema of each input and response — read off the live bus, so
a workflow is checked against what really runs, and an agent writes one against what really
exists.

    operations = operations_of(billing)
    quote = operations["CommandQuote"]
    quote.required_input    ->  {"amount"}
    quote.response_fields   ->  {"total", "currency"}
    quote.to_json()         ->  {"description": "...", "input": {...}, "response": {...}}
"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from sincpro_framework.introspection.inspector import app_services, features
from sincpro_framework.use_bus import UseFramework


@dataclass(frozen=True)
class OperationDescription:
    """One Command the bus executes: its name, its input model, what it answers, and the
    description its handler gives."""

    name: str
    input: type[BaseModel]
    response: Any
    """The declared response: a model, `NoneType` for one that answers nothing, or `None` when
    it is not declared."""

    description: str

    @property
    def response_fields(self) -> set[str] | None:
        """The fields a later step may reference; `None` when the response is not declared, so
        they cannot be checked.

            OperationDescription(response=ResponseInvoice, …).response_fields  ->  {"id", "total", …}
            OperationDescription(response=NoneType, …).response_fields         ->  set()  # no answer
            OperationDescription(response=None, …).response_fields             ->  None   # undeclared
            OperationDescription(response=dict[str, int], …).response_fields   ->  None   # no model

        Only a Pydantic model has fields to name; any other declared type is as unknown.
        """
        if self.response is type(None):
            return set()
        if isinstance(self.response, type) and issubclass(self.response, BaseModel):
            return set(self.response.model_fields)
        return None

    @property
    def required_input(self) -> set[str]:
        """The input fields with no default — what a workflow step must fill."""
        return {
            name for name, field in self.input.model_fields.items() if field.is_required()
        }

    def to_json(self) -> dict[str, Any]:
        """The operation as JSON Schema, for an agent or a UI — `response` is `None` unless it
        is a Pydantic model."""
        is_model = isinstance(self.response, type) and issubclass(self.response, BaseModel)
        return {
            "description": self.description,
            "input": self.input.model_json_schema(),
            "response": self.response.model_json_schema() if is_model else None,
        }


def operations_of(bus: UseFramework) -> dict[str, OperationDescription]:
    """Every operation of the bus — built now if it was not yet; a reference is described from
    its registrations."""
    bus.build_root_bus()
    described = {**features(bus), **app_services(bus)}
    return {
        name: OperationDescription(
            name, metadata.dto, metadata.response, metadata.description
        )
        for name, metadata in sorted(described.items())
    }
