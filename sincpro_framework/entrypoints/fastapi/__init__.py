"""REST on FastAPI (PRD_15 §2) — the `[fastapi]` extra. The generated routers, and the pieces a
route of the project's uses to run the exact same path.

    from sincpro_framework.entrypoints.fastapi import FastApiGateway, bus_call

Context: FastAPI is imported only here — importing the framework, or `entrypoints.rest`'s
wire-neutral route table, never needs it.
"""

FASTAPI_MISSING = (
    "FastAPI is not installed. Install with: pip install sincpro-framework[fastapi]"
)

try:
    import fastapi as _fastapi  # noqa: F401
except ImportError as error:  # pragma: no cover - tests/test_core_without_extras.py
    raise ImportError(FASTAPI_MISSING) from error

from sincpro_framework.entrypoints.fastapi.calling import (
    IDEMPOTENCY_KEY,
    BusCall,
    RequestContext,
    acting_credentials,
    bus_call,
    request_context,
)
from sincpro_framework.entrypoints.fastapi.gateway import (
    FastApiGateway,
    FastApiWire,
    operation_extra,
    problem_responses,
)
from sincpro_framework.entrypoints.fastapi.problems import (
    PROBLEM_JSON,
    IdempotencyKeyMissing,
    Problem,
    ProblemError,
    ProblemMiddleware,
    install_problem_handlers,
    problem_of,
)

__all__ = [
    "IDEMPOTENCY_KEY",
    "PROBLEM_JSON",
    "BusCall",
    "FastApiGateway",
    "FastApiWire",
    "IdempotencyKeyMissing",
    "Problem",
    "ProblemError",
    "ProblemMiddleware",
    "RequestContext",
    "acting_credentials",
    "bus_call",
    "install_problem_handlers",
    "operation_extra",
    "problem_of",
    "problem_responses",
    "request_context",
]
