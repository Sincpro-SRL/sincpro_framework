"""Execute a Scalar payload against a UseFramework instance — dict in, dict out.

UseFramework's own contract stays typed (DTO in, DTO out); it never learns about
dicts or JSON. This module is the boundary that does that marshalling, so any
wire host (MCP, JSON-RPC, tomorrow REST/CLI) can call a Feature/ApplicationService
with a plain Scalar instead of constructing the DTO itself.
"""

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from sincpro_framework.entrypoints.const import TRACE_KEYS, RunFn, Scalar
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework


def dump_scalar_result(result: Any) -> Scalar:
    """Turn a bus response into a JSON-safe Scalar.

    Pydantic keeps arbitrary objects (a Zeep SOAP response on an ``Any`` field) in
    the dump instead of raising, so the failure would otherwise surface inside the
    host, after the Feature already ran its side effect.

    1. None becomes {}.
    2. A Pydantic model dumps in JSON mode; fall back to Python dump if that fails.
    3. A dict passes through; any other value is wrapped as {result: value}.
    4. Return the payload untouched when json.dumps accepts it.
    5. Final: else stringify the offending leaves and warn naming the response.
    """
    if result is None:
        return {}
    if isinstance(result, BaseModel):
        try:
            dumped = result.model_dump(mode="json")
        except Exception:
            dumped = result.model_dump()
        payload = dumped if isinstance(dumped, dict) else {"result": dumped}
    elif isinstance(result, dict):
        payload = result
    else:
        payload = {"result": result}

    try:
        json.dumps(payload)
        return payload
    except (TypeError, ValueError):
        logger.warning(
            "Non-JSON value in [%s] response: coerced to string", type(result).__name__
        )
        return json.loads(json.dumps(payload, default=str))


def extract_executor_fn(
    framework_instance: UseFramework, dto_type: type[DataTransferObject]
) -> RunFn:
    """Close over the DTO so a Scalar becomes a bus call.

    A factory, not a closure written inline in a loop: each entry needs its own
    `dto_type` bound at closure-creation time, not the loop variable's final value.

    1. Validate the payload as the DTO (Value Objects run here).
    2. Execute through UseFramework.
    3. Final: dump the response as a Scalar.
    """

    def run(payload: Scalar) -> Scalar:
        result = framework_instance(dto_type.model_validate(payload))
        return dump_scalar_result(result)

    return run


def execute(
    framework_instance: UseFramework,
    run: RunFn,
    payload: Scalar,
    context: Mapping[str, Any] | None = None,
) -> Scalar:
    """Run one bound Scalar execution inside framework.context / with_trace when asked.

    1. Split tracing keys (trace_id, span_id, carrier) from the rest of the context.
    2. Enter framework.context with the remaining keys when any remain.
    3. Enter framework.with_trace when tracing keys are present.
    4. Final: run(payload) sees self.context and the OTel parent when configured.
    """
    extra = dict(context or {})
    trace_kwargs: dict[str, Any] = {}
    for key in TRACE_KEYS:
        if key not in extra:
            continue
        value = extra.pop(key)
        if value is not None:
            trace_kwargs[key] = value

    if extra and trace_kwargs:
        with framework_instance.context(extra):
            with framework_instance.with_trace(**trace_kwargs):
                return run(payload)
    if extra:
        with framework_instance.context(extra):
            return run(payload)
    if trace_kwargs:
        with framework_instance.with_trace(**trace_kwargs):
            return run(payload)
    return run(payload)
