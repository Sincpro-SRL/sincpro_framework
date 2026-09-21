"""What a wire publishes and returns for every response shape the bus admits.

The bus discards `execute(dto, return_type)`'s return_type at runtime, so the
catalog reads the declaration. And `TypeDTO`/`TypeDTOResponse` admit a dataclass
— an `Entity` is one — not only a DataTransferObject.
"""

import json
from dataclasses import dataclass
from typing import Any

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd import Entity
from sincpro_framework.entrypoints.catalog import Catalog
from sincpro_framework.entrypoints.grpc import GrpcGateway
from sincpro_framework.entrypoints.rpc import RpcGateway
from sincpro_framework.entrypoints.scalar_executor import dump_scalar_result
from sincpro_framework.introspection import inspector


@dataclass
class MappedContact:
    """A domain mapped to its table imperatively is a plain dataclass, not a DTO."""

    identifier: str
    total: int


@dataclass
class Invoice(Entity):
    number: str = ""


class AskModel(DataTransferObject):
    n: int


class ModelResponse(DataTransferObject):
    total: int


class AskDataclass(DataTransferObject):
    identifier: str


class AskEntity(DataTransferObject):
    number: str


class AskList(DataTransferObject):
    n: int


class AskUndeclared(DataTransferObject):
    n: int


class AskGenericOnly(DataTransferObject):
    n: int


@dataclass
class DataclassCommand:
    """A Command declared as a dataclass, which the bus's TypeDTO admits."""

    identifier: str
    retries: int = 3


class AskOrchestration(DataTransferObject):
    n: int


def _instance(name: str = "shapes") -> UseFramework:
    framework = UseFramework(name, log_after_execution=False)

    @framework.feature(AskModel)
    class ReturnsModel(Feature):
        def execute(self, dto: AskModel) -> ModelResponse:
            return ModelResponse(total=dto.n)

    @framework.feature(AskDataclass)
    class ReturnsDataclass(Feature):
        def execute(self, dto: AskDataclass) -> MappedContact:
            return MappedContact(identifier=dto.identifier, total=7)

    @framework.feature(AskEntity)
    class ReturnsEntity(Feature):
        def execute(self, dto: AskEntity) -> Invoice:
            return Invoice(number=dto.number)

    @framework.feature(AskList)
    class ReturnsList(Feature):
        def execute(self, dto: AskList) -> list[ModelResponse]:
            return [ModelResponse(total=index) for index in range(dto.n)]

    @framework.feature(AskUndeclared)
    class ReturnsUndeclared(Feature):
        def execute(self, dto):
            return ModelResponse(total=dto.n)

    @framework.feature(AskGenericOnly)
    class ReturnsGenericOnly(Feature[AskGenericOnly, ModelResponse, None]):
        def execute(self, dto):
            return ModelResponse(total=dto.n)

    @framework.feature(DataclassCommand)
    class TakesDataclassCommand(Feature):
        def execute(self, dto: DataclassCommand) -> ModelResponse:
            return ModelResponse(total=dto.retries)

    @framework.app_service(AskOrchestration)
    class Orchestrates(ApplicationService):
        def execute(self, dto: AskOrchestration) -> MappedContact:
            return MappedContact(identifier="from-app-service", total=dto.n)

    framework.build_root_bus()
    return framework


def _packed(framework: UseFramework) -> dict[str, Any]:
    return {entry.name: entry for entry in Catalog(framework).get_scalar_use_cases()}


# --- the declaration the catalog reads -------------------------------------


def test_response_is_read_from_the_execute_annotation():
    described = inspector.features(_instance())

    assert described["AskModel"].response is ModelResponse
    assert described["AskDataclass"].response is MappedContact
    assert described["AskList"].response == list[ModelResponse]


def test_response_falls_back_to_the_generic_parameter():
    """`Feature[Command, Response, Ctx]` with an unannotated execute still declares."""
    described = inspector.features(_instance())

    assert described["AskGenericOnly"].response is ModelResponse


def test_undeclared_response_publishes_no_shape():
    described = inspector.features(_instance())

    assert described["AskUndeclared"].response is None
    assert _packed(_instance())["AskUndeclared"].response_json_schema is None


# --- the schema each wire publishes ----------------------------------------


def test_catalog_publishes_the_response_schema_for_every_shape():
    packed = _packed(_instance())

    assert packed["AskModel"].response_json_schema == ModelResponse.model_json_schema()
    assert set(packed["AskDataclass"].response_json_schema["properties"]) == {
        "identifier",
        "total",
    }
    assert packed["AskList"].response_json_schema["type"] == "array"
    assert "number" in packed["AskEntity"].response_json_schema["properties"]


def test_openrpc_result_carries_the_response_schema():
    document = RpcGateway({"shapes": _instance()}).discover()
    methods = {method["name"]: method for method in document["methods"]}

    result = methods["shapes.features.AskDataclass"]["result"]["schema"]
    assert set(result["properties"]) == {"identifier", "total"}
    assert methods["shapes.features.AskUndeclared"]["result"]["schema"] == {"type": "object"}


def test_grpc_describe_and_proto_carry_the_response_shape():
    gateway = GrpcGateway({"shapes": _instance()})
    document = gateway.describe()
    methods = {
        method["name"]: method
        for service in document["services"]
        for method in service["methods"]
    }

    assert set(methods["AskDataclass"]["result"]["properties"]) == {"identifier", "total"}
    assert methods["AskUndeclared"]["result"] == {"type": "object"}
    assert "// Struct: the fields of MappedContact." in gateway.proto_files()["shapes.proto"]


# --- what actually comes back over the wire --------------------------------


def test_dataclass_response_is_a_real_object_not_its_repr():
    """`{"result": "MappedContact(identifier='x', total=7)"}` is not an answer."""
    packed = _packed(_instance())

    assert packed["AskDataclass"].run({"identifier": "x"}) == {
        "identifier": "x",
        "total": 7,
    }


def test_entity_response_is_dumped_field_by_field():
    result = _packed(_instance())["AskEntity"].run({"number": "F-1"})

    assert result["number"] == "F-1"
    assert isinstance(result["id"], str)
    assert isinstance(result["created_at"], str)
    assert json.dumps(result)


def test_list_of_models_keeps_its_elements():
    result = _packed(_instance())["AskList"].run({"n": 3})

    assert result == {"result": [{"total": 0}, {"total": 1}, {"total": 2}]}


def test_app_service_dataclass_response_travels_too():
    result = _packed(_instance())["AskOrchestration"].run({"n": 2})

    assert result == {"identifier": "from-app-service", "total": 2}


def test_unrenderable_leaf_is_stringified_not_raised():
    class Opaque:
        def __str__(self) -> str:
            return "opaque-value"

    dumped = dump_scalar_result({"label": "monthly", "raw": Opaque()})

    assert dumped == {"label": "monthly", "raw": "opaque-value"}
    assert json.dumps(dumped)


# --- a Command that is a dataclass -----------------------------------------


def test_dataclass_command_is_described_and_schemad():
    packed = _packed(_instance())["DataclassCommand"]

    assert set(packed.json_schema["properties"]) == {"identifier", "retries"}
    assert packed.json_schema["required"] == ["identifier"]
    assert packed.description.startswith("A Command declared as a dataclass")


def test_dataclass_command_executes_from_a_scalar():
    assert _packed(_instance())["DataclassCommand"].run({"identifier": "x"}) == {"total": 3}
    assert _packed(_instance())["DataclassCommand"].run(
        {"identifier": "x", "retries": 9}
    ) == {"total": 9}


def test_dataclass_command_rejects_a_bad_payload_as_invalid_params():
    from sincpro_framework.entrypoints.rpc.jrpc import INVALID_PARAMS

    gateway = RpcGateway({"shapes": _instance()})
    reply = gateway.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "shapes.features.DataclassCommand",
            "params": {"retries": "not-an-int"},
        }
    )

    assert isinstance(reply, dict)
    assert reply["error"]["code"] == INVALID_PARAMS


def test_dataclass_command_becomes_an_mcp_tool_signature():
    import inspect as inspect_module

    from sincpro_framework.entrypoints.mcp.mcp import fastmcp_callable

    tool = fastmcp_callable(_packed(_instance())["DataclassCommand"])
    parameters = inspect_module.signature(tool).parameters

    assert set(parameters) == {"identifier", "retries"}
    assert parameters["identifier"].default is inspect_module.Parameter.empty
    assert parameters["retries"].default == 3


@pytest.fixture
def grpc_client():
    from sincpro_framework.entrypoints.grpc.client import GrpcClient

    gateway = GrpcGateway({"shapes": _instance()})
    server = gateway.server(max_workers=2)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    client = GrpcClient(f"127.0.0.1:{port}")
    try:
        yield client
    finally:
        client.close()
        server.stop(None)


def test_every_shape_round_trips_over_grpc(grpc_client):
    """A Struct number is a double in both directions: `retries: int` returns 3.0."""
    assert grpc_client.call("/shapes.Features/AskDataclass", {"identifier": "x"}) == {
        "identifier": "x",
        "total": 7,
    }
    assert grpc_client.call("/shapes.Features/AskList", {"n": 2}) == {
        "result": [{"total": 0}, {"total": 1}]
    }
    assert grpc_client.call("/shapes.Features/DataclassCommand", {"identifier": "x"}) == {
        "total": 3.0
    }
    assert (
        grpc_client.call("/shapes.Features/AskEntity", {"number": "F-9"})["number"] == "F-9"
    )
