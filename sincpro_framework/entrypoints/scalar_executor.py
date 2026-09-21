"""Execute a Scalar payload against a UseFramework instance — dict in, dict out.

UseFramework's own contract stays typed (DTO in, DTO out); it never learns about
dicts or JSON. This module is the boundary that does that marshalling, so any
wire host (MCP, JSON-RPC, tomorrow REST/CLI) can call a Feature/ApplicationService
with a plain Scalar instead of constructing the DTO itself.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from sincpro_framework.entrypoints import json_utils
from sincpro_framework.entrypoints.const import TRACE_KEYS, RunFn, Scalar
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework


def dump_scalar_result(result: Any) -> Scalar:
    """Turn a bus response into a JSON-safe Scalar.

    The bus answers whatever `execute` returns: a DTO, a dataclass (an `Entity`
    is one), a `list` of either, a dict, a scalar, or nothing. Pydantic's
    serializer renders all of them, and `fallback` catches the leaf it cannot —
    a Zeep SOAP response parked on an `Any` field — instead of raising inside the
    host, after the Feature already ran its side effect.

    1. None becomes {}.
    2. Everything else is rendered in JSON mode, leaf by leaf.
        2.1 A leaf Pydantic cannot render is stringified, and warned about naming
            its own type — not the response's, which is rarely the culprit.
    3. Final: a dict passes through; any other value is wrapped as {result: value}.
    """
    if result is None:
        return {}

    coerced: list[str] = []

    def stringify(value: Any) -> str:
        coerced.append(type(value).__name__)
        return str(value)

    payload = to_jsonable_python(result, fallback=stringify, serialize_unknown=True)
    if coerced:
        logger.warning(
            "Non-JSON value in [%s] response: [%s] coerced to string",
            type(result).__name__,
            ", ".join(dict.fromkeys(coerced)),
        )
    return payload if isinstance(payload, dict) else {"result": payload}


def extract_executor_fn(framework_instance: UseFramework, dto_type: Any) -> RunFn:
    """Close over the DTO so a Scalar becomes a bus call.

    A factory, not a closure written inline in a loop: each entry needs its own
    `dto_type` bound at closure-creation time, not the loop variable's final value.

    1. Validate the payload as the DTO (Value Objects run here). A dataclass
       Command validates through a TypeAdapter and raises the same
       `ValidationError` the wires already map to an invalid-params status.
    2. Execute through UseFramework.
    3. Final: dump the response as a Scalar.
    """
    validate = (
        dto_type.model_validate
        if isinstance(dto_type, type) and issubclass(dto_type, BaseModel)
        else json_utils.adapter(dto_type).validate_python
    )

    def run(payload: Scalar) -> Scalar:
        return dump_scalar_result(framework_instance(validate(payload)))

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
