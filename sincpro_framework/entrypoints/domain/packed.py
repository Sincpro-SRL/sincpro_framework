"""One use case as a catalog packs it for a JSON-speaking wire — its name, schemas and the
callables a wire runs it through.

    packed = Catalog(billing).get_scalar_use_cases()[0]
    packed.name                                          ->  "CommandIssueInvoice"
    packed.run({"invoice_id": 7})                        ->  {"status": "issued"}
    packed.call({"invoice_id": 7}, None, None, EntrypointKind.MCP)
    ->  {"status": "issued"}, inside a context opened as an MCP entrypoint
"""

from typing import Any

from sincpro_framework.entrypoints.domain.layers import CallFn, Layer, RunFn
from sincpro_framework.sincpro_abstractions import DataTransferObject


class PackedFeatureOrAppService(DataTransferObject):
    """One Feature or ApplicationService, packed for a JSON-speaking driving adapter.

    `layer` is "features" or "app_services" — the bus's own vocabulary; also names
    the RPC method namespace (`{alias}.{layer}.{DtoName}`) and the MCP/OpenRPC tag.
    `json_schema` is the input DTO's schema and `response_json_schema` the declared
    response's, both computed once here — hosts must not recompute them.
    `response_json_schema` is None when `execute` declares no return type: a wire
    then publishes an open object rather than a shape nobody promised.
    `run` and `call` are bound callables, not JSON; DataTransferObject allows arbitrary types
    so they can travel alongside the JSON-safe fields.
    """

    name: str
    layer: Layer
    description: str
    dto: type
    json_schema: dict[str, Any]
    response_json_schema: dict[str, Any] | None
    run: RunFn
    call: CallFn
    """`run` inside the context, the trace and the identity a wire carried — what a wire calls."""
    handler: type | None = None
    """The Feature or ApplicationService answering `dto` — what exposure reads its bindings,
    its access and its stages from."""
    response: Any = None
    """What `execute` declares it answers, as an annotation — `None` for nothing declared."""
