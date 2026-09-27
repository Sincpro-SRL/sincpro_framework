"""What a bus can execute, with the schema of each input and response — read off the live bus, so
a workflow is checked against what really runs, and an agent writes one against what really
exists."""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from sincpro_framework.introspection.inspector import app_services, features
from sincpro_framework.use_bus import UseFramework


@dataclass(frozen=True)
class Operation:
    name: str
    input: type[BaseModel]
    response: Any
    """The declared response: a model, `NoneType` for one that answers nothing, or `None` when
    it is not declared."""

    description: str

    @property
    def response_fields(self) -> set[str] | None:
        """The fields a later step may reference; `None` when the response is not declared, so
        they cannot be checked."""
        if self.response is type(None):
            return set()
        if isinstance(self.response, type) and issubclass(self.response, BaseModel):
            return set(self.response.model_fields)
        return None

    @property
    def required_input(self) -> set[str]:
        return {
            name for name, field in self.input.model_fields.items() if field.is_required()
        }

    def to_json(self) -> dict[str, Any]:
        is_model = isinstance(self.response, type) and issubclass(self.response, BaseModel)
        return {
            "description": self.description,
            "input": self.input.model_json_schema(),
            "response": self.response.model_json_schema() if is_model else None,
        }


def operations_of(bus: UseFramework) -> dict[str, Operation]:
    """Every operation of the bus — built now if it was not yet."""
    if not bus.was_initialized:
        bus.build_root_bus()
    described = {**features(bus), **app_services(bus)}
    return {
        name: Operation(name, metadata.dto, metadata.response, metadata.description)
        for name, metadata in sorted(described.items())
    }
