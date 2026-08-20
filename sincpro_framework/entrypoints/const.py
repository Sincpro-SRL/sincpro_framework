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
