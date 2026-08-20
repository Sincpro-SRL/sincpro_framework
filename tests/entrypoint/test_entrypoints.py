"""entrypoint_mcp: catalog, MCP host, Field descriptions, Value Object JSON roundtrip."""

import inspect
import json
from typing import Any
from uuid import uuid4

from pydantic import Field

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd import ValueObject
from sincpro_framework.entrypoints.json_utils import dto_is_json_serializable
from sincpro_framework.entrypoints.mcp import Entrypoint, build_mcp_server
from sincpro_framework.entrypoints.mcp.mcp import fastmcp_callable

NIT = ValueObject(int, lambda v: abs(v), name="NIT")
Email = ValueObject(str, lambda v: v.strip().lower(), name="Email")


class ValidateCard(DataTransferObject):
    """Check a card number before charging."""

    card_number: str
    """PAN to validate."""
    cvv: str = Field(description="Card verification value")


class ValidateCardResponse(DataTransferObject):
    valid: bool
    card_number: str


class ChargePayment(DataTransferObject):
    nit: NIT
    email: Email
    amount: float


class ChargePaymentResponse(DataTransferObject):
    nit: NIT
    email: Email
    charged: bool


class SendBinaryPackage(DataTransferObject):
    name: str
    payload: bytes


class SendBinaryPackageResponse(DataTransferObject):
    ok: bool


class OrchestrateCharge(DataTransferObject):
    nit: NIT
    email: Email
    amount: float


class ScheduleTask(DataTransferObject):
    """Queue a task with optional metadata."""

    name: str
    tags: list[str] = Field(default_factory=list)
    token: str = Field(default_factory=lambda: uuid4().hex)
    retries: int = 3


class ScheduleTaskResponse(DataTransferObject):
    name: str
    tags: list[str]
    token: str
    retries: int


class SoapLike:
    """Stand-in for a Zeep object kept on an Any field."""


class ReportRaw(DataTransferObject):
    """Return a response that carries a non-JSON object."""

    label: str


class ReportRawResponse(DataTransferObject):
    label: str
    raw_response: Any = None


def _build_framework() -> UseFramework:
    framework = UseFramework("entrypoint-test", log_after_execution=False)

    @framework.feature(ValidateCard)
    class ValidateCardFeature(Feature):
        """Validate a payment card (atomic)."""

        def execute(self, dto: ValidateCard) -> ValidateCardResponse:
            return ValidateCardResponse(
                valid=len(dto.card_number) >= 4, card_number=dto.card_number
            )

    @framework.feature(ChargePayment)
    class ChargePaymentFeature(Feature):
        """Charge using domain value objects."""

        def execute(self, dto: ChargePayment) -> ChargePaymentResponse:
            return ChargePaymentResponse(nit=dto.nit, email=dto.email, charged=True)

    @framework.feature(SendBinaryPackage)
    class SendBinaryPackageFeature(Feature):
        def execute(self, dto: SendBinaryPackage) -> SendBinaryPackageResponse:
            return SendBinaryPackageResponse(ok=bool(dto.payload))

    @framework.app_service(OrchestrateCharge)
    class OrchestrateChargeService(ApplicationService):
        """Orchestrate validate + charge."""

        def execute(self, dto: OrchestrateCharge) -> ChargePaymentResponse:
            return self.feature_bus.execute(
                ChargePayment(nit=dto.nit, email=dto.email, amount=dto.amount),
                ChargePaymentResponse,
            )

    @framework.feature(ScheduleTask)
    class ScheduleTaskFeature(Feature):
        """Queue a task, echoing the defaults the DTO resolved."""

        def execute(self, dto: ScheduleTask) -> ScheduleTaskResponse:
            return ScheduleTaskResponse(
                name=dto.name, tags=dto.tags, token=dto.token, retries=dto.retries
            )

    @framework.feature(ReportRaw)
    class ReportRawFeature(Feature):
        """Respond with a SOAP-like object on an Any field."""

        def execute(self, dto: ReportRaw) -> ReportRawResponse:
            return ReportRawResponse(label=dto.label, raw_response=SoapLike())

    framework.build_root_bus()
    return framework


def test_tools_include_features_and_application_services():
    entrypoint = Entrypoint(_build_framework())
    names = {tool.name for tool in entrypoint.tools()}
    layers = {tool.name: tool.layer for tool in entrypoint.tools()}

    assert "ValidateCard" in names
    assert "ChargePayment" in names
    assert "OrchestrateCharge" in names
    assert layers["ValidateCard"] == "features"
    assert layers["OrchestrateCharge"] == "app_services"


def test_own_docstring_not_feature_base():
    tools = {tool.name: tool for tool in Entrypoint(_build_framework()).tools()}

    assert "atomic" in tools["ValidateCard"].description.lower()
    assert "Second layer of the framework" not in tools["OrchestrateCharge"].description
    assert "Orchestrate" in tools["OrchestrateCharge"].description


def test_mcp_skips_bytes_dtos():
    assert dto_is_json_serializable(ValidateCard) is True
    assert dto_is_json_serializable(SendBinaryPackage) is False


def test_to_callables_executes_atomic_feature():
    ops = Entrypoint(_build_framework()).to_callables()
    result = ops["ValidateCard"]({"card_number": "4111", "cvv": "123"})

    assert result == {"valid": True, "card_number": "4111"}


def test_value_object_json_roundtrip_runs_validate_fn():
    ops = Entrypoint(_build_framework()).to_callables()
    result = ops["ChargePayment"]({"nit": -99001, "email": "  A@B.COM ", "amount": 10.5})

    assert result["nit"] == 99001
    assert result["email"] == "a@b.com"
    assert result["charged"] is True


def test_include_and_exclude_subset():
    names = {
        tool.name
        for tool in (
            Entrypoint(_build_framework())
            .include(ValidateCard, OrchestrateCharge)
            .exclude(OrchestrateCharge)
            .tools()
        )
    }
    assert names == {"ValidateCard"}


def test_wrap_single_operation():
    def tag(run):
        def wrapped(payload):
            result = run(payload)
            result["wrapped"] = True
            return result

        return wrapped

    ops = Entrypoint(_build_framework()).wrap(ValidateCard, tag).to_callables()
    result = ops["ValidateCard"]({"card_number": "4111", "cvv": "123"})
    charged = ops["ChargePayment"]({"nit": 1, "email": "a@b.com", "amount": 1})

    assert result["wrapped"] is True
    assert "wrapped" not in charged


def test_field_description_does_not_change_constructor():
    dto = ValidateCard(card_number="4111", cvv="123")
    assert dto.card_number == "4111"
    assert dto.cvv == "123"

    same = ValidateCard.model_validate({"card_number": "4111", "cvv": "123"})
    assert same == dto


def test_plain_dto_without_field_still_constructs():
    dto = ChargePayment(nit=-5, email="  A@B.COM ", amount=1.0)
    assert dto.nit == 5
    assert dto.email == "a@b.com"


def test_dto_field_descriptions_in_schema():
    tools = {tool.name: tool for tool in Entrypoint(_build_framework()).tools()}
    properties = tools["ValidateCard"].json_schema["properties"]

    assert properties["cvv"]["description"] == "Card verification value"
    assert properties["card_number"].get("description") == "PAN to validate."


def test_fastmcp_callable_signature_is_dto_fields():
    """FastMCP 3 builds JSON Schema from the function signature, not from Tool.json_schema."""
    tools = {tool.name: tool for tool in Entrypoint(_build_framework()).tools()}
    fn = fastmcp_callable(tools["ValidateCard"])
    parameters = inspect.signature(fn).parameters

    assert fn.__name__ == "ValidateCard"
    assert "atomic" in (fn.__doc__ or "").lower()
    assert set(parameters) == {"card_number", "cvv"}
    assert all(p.kind == inspect.Parameter.KEYWORD_ONLY for p in parameters.values())
    assert parameters["card_number"].annotation is str
    assert parameters["cvv"].annotation is str


def test_build_mcp_server_requires_extra_or_returns_host():
    try:
        server = build_mcp_server(_build_framework())
        assert server is not None
    except ImportError as error:
        assert "sincpro-framework[mcp]" in str(error)


def test_value_object_title_in_input_schema():
    tools = {tool.name: tool for tool in Entrypoint(_build_framework()).tools()}
    nit_schema = tools["ChargePayment"].json_schema["properties"]["nit"]
    email_schema = tools["ChargePayment"].json_schema["properties"]["email"]

    assert nit_schema["title"] == "NIT"
    assert nit_schema["type"] == "integer"
    assert email_schema["title"] == "Email"
    assert email_schema["type"] == "string"


def test_default_factory_field_stays_optional_and_runs_per_call():
    """A default_factory must not be frozen into the signature as a fixed value."""
    ops = Entrypoint(_build_framework()).to_callables()

    first = ops["ScheduleTask"]({"name": "sync"})
    second = ops["ScheduleTask"]({"name": "sync"})

    assert first["tags"] == []
    assert first["retries"] == 3
    assert first["token"] != second["token"]


def test_default_factory_field_is_not_required_in_published_schema():
    tools = {tool.name: tool for tool in Entrypoint(_build_framework()).tools()}
    fn = fastmcp_callable(tools["ScheduleTask"])
    parameters = inspect.signature(fn).parameters

    assert parameters["retries"].default == 3
    assert parameters["name"].default is inspect.Parameter.empty

    try:
        import anyio
        from fastmcp import FastMCP  # pyright: ignore[reportMissingImports]
    except ImportError:
        return

    server = FastMCP("schema-check")
    server.tool(fn, name="ScheduleTask", description="test")

    async def published_schema() -> dict:
        published = await server.list_tools()
        return published[0].parameters

    schema = anyio.run(published_schema)
    assert schema["required"] == ["name"]
    assert set(schema["properties"]) == {"name", "tags", "token", "retries"}


def test_non_json_response_is_coerced_and_keeps_the_other_fields():
    ops = Entrypoint(_build_framework()).to_callables()

    result = ops["ReportRaw"]({"label": "monthly"})

    assert result["label"] == "monthly"
    assert isinstance(result["raw_response"], str)
    assert json.dumps(result)
