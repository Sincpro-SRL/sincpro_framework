"""The shared vocabulary of the entrypoints: which layer a use case runs in, the Scalar a wire
hands it, the `run` it becomes a bus call through, and the `call` every wire makes.

    run: RunFn = catalog.get_scalar_use_cases()[0].run
    run({"invoice_id": 7})                   ->  {"status": "issued"}
    call: CallFn = catalog.get_scalar_use_cases()[0].call
    call({"invoice_id": 7}, {"tenant_id": "acme"}, credentials, EntrypointKind.RPC)
    ->  {"status": "issued"}, run inside acme's context, as whoever the credentials say
    audited: Wrapper = lambda run: lambda payload: log_and(run, payload)

Context: a wire speaks dicts, the bus speaks DTOs; `Scalar` is the dict that crosses, `RunFn`
the callable that turns it into a bus call, `CallFn` that same run inside the context, the trace
and the identity the wire carried — what an adapter calls, so it never opens them itself —
and `Wrapper` what `Catalog.wrap` decorates a run with.
The binary types and formats say what cannot travel as JSON; `TRACE_KEYS` what a context
carries for the trace rather than for the use case.
"""

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any

from sincpro_framework.auth import Credentials
from sincpro_framework.context import EntrypointKind


class Layer(StrEnum):
    """Which bus registry a Feature or ApplicationService lives in — the bus's
    own vocabulary. Names the RPC method namespace ({alias}.{layer}.{DtoName})
    and the MCP/OpenRPC tag; there's no second axis, so this is the only field."""

    FEATURES = "features"
    APP_SERVICES = "app_services"


type Scalar = dict[str, Any]

BINARY_TYPES = (bytes, bytearray, memoryview)
BINARY_JSON_FORMATS = {"binary", "byte"}
TRACE_KEYS = ("trace_id", "span_id", "carrier")
RunFn = Callable[[Scalar], Scalar]
CallFn = Callable[
    [Scalar, Mapping[str, Any] | None, Credentials | None, EntrypointKind], Scalar
]
Wrapper = Callable[[RunFn], RunFn]
