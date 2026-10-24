"""The shared vocabulary of the entrypoints: which layer a use case runs in, the Scalar a wire
hands it, and the `run` every wire calls.

    run: RunFn = catalog.get_scalar_use_cases()[0].run
    run({"invoice_id": 7})                   ->  {"status": "issued"}
    audited: Wrapper = lambda run: lambda payload: log_and(run, payload)

Context: a wire speaks dicts, the bus speaks DTOs; `Scalar` is the dict that crosses, `RunFn`
the callable that turns it into a bus call, `Wrapper` what `Catalog.wrap` decorates it with.
The binary types and formats say what cannot travel as JSON; `TRACE_KEYS` what a context
carries for the trace rather than for the use case.
"""

from enum import StrEnum
from typing import Any, Callable


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
Wrapper = Callable[[RunFn], RunFn]
