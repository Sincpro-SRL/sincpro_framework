"""The one call path of the REST wire (PRD_15 §1.2): what a generated route runs, and what a route
of the project's runs through `Depends(bus_call(bus))` — so neither skips a component.

    @router.post("/invoices", status_code=201)
    async def issue(cmd: CommandIssueInvoice, call: BusCall = Depends(bus_call(billing))) -> Issued:
        return await call(cmd)

Context — the contextvar trap. Identity is a `ContextVar`, and FastAPI runs each synchronous
dependency in a worker thread with a *copy* of the context: an identity set there is gone in the
next thread. So the credentials are only read here, in an async dependency with no I/O, and
authentication and `bus(dto)` run together in one worker thread (`anyio.to_thread`). The bus is
called, never `execute`: the access guard, idempotency, caching, the Feature's span and Sentry
apply by construction. A FastAPI dependency is never authorization — `AccessControl` inside the
bus stays the only guard.
"""

from collections.abc import Awaitable, Callable, Mapping
from contextlib import ExitStack
from functools import partial
from typing import Any

import anyio.to_thread
from anyio import CapacityLimiter
from fastapi import Depends, Request
from pydantic import ConfigDict

from sincpro_framework.auth.domain import Credentials
from sincpro_framework.auth.transports import authenticated_as, credentials_from_asgi
from sincpro_framework.caching import IDEMPOTENCY_KEY, declares_once
from sincpro_framework.caching.domain.idempotent_command import IdempotentCommand
from sincpro_framework.entrypoints.fastapi.problems import (
    BUSES_STATE,
    TRACE_STATE,
    IdempotencyKeyMissing,
)
from sincpro_framework.observability import process
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.use_bus import UseFramework

type BusCall = Callable[[Any], Awaitable[Any]]
"""`await call(dto)` — the bus's answer, or its failure raised as itself."""


class RequestContext(DataTransferObject):
    """What of a request reaches the bus besides the DTO and the credentials."""

    model_config = ConfigDict(frozen=True)

    idempotency_key: str | None = None
    traceparent: str | None = None
    tracestate: str | None = None
    correlation_id: str | None = None

    def bus_context(self) -> dict[str, Any]:
        found: dict[str, Any] = {}
        if self.idempotency_key:
            found[IDEMPOTENCY_KEY] = self.idempotency_key
        if self.correlation_id:
            found["correlation_id"] = self.correlation_id
        return found

    @property
    def trace_id(self) -> str | None:
        parts = (self.traceparent or "").split("-")
        return parts[1] if len(parts) >= 3 and len(parts[1]) == 32 else None


async def acting_credentials(request: Request) -> Credentials:
    """Who the request says is calling — read, never checked: the bus's `AccessControl`
    authenticates them, in the thread that runs the use case. No I/O, so async."""
    return credentials_from_asgi(request.scope)


async def request_context(request: Request) -> RequestContext:
    """The `Idempotency-Key`, the W3C trace headers and the correlation id of the request."""
    headers = request.headers
    return RequestContext(
        idempotency_key=headers.get("idempotency-key") or None,
        traceparent=headers.get("traceparent") or None,
        tracestate=headers.get("tracestate") or None,
        correlation_id=headers.get("x-correlation-id") or None,
    )


def requires_idempotency_key(bus: UseFramework, command: type) -> bool:
    """A use case that runs once, whose Command says nothing of what identifies it: only the
    header tells a retry from a new request with the same fields."""
    handler = bus.handler_of(command)
    return (
        handler is not None
        and declares_once(handler)
        and not issubclass(command, IdempotentCommand)
    )


def _host_span_active() -> bool:
    return bool(process.trace_ids())


def run_on_bus(
    bus: UseFramework,
    dto: Any,
    credentials: Credentials | None,
    context: RequestContext,
    seen: dict[str, str | None],
) -> Any:
    """1. Authenticate `credentials` by the bus's `AccessControl` — in this thread.
    2. The request's context keys (`Idempotency-Key`, correlation id) into the bus context.
    3. The trace: the host's span when its instrumentation opened one, else the caller's
       `traceparent`; no second root.
    4. Final: `bus(dto)`; on failure, the trace it belongs to is kept in `seen`.
    """
    with ExitStack() as stack:
        stack.enter_context(authenticated_as(bus, credentials))
        extra = context.bus_context()
        if extra:
            stack.enter_context(bus.context(extra))
        if _host_span_active():
            stack.enter_context(bus.with_parent_trace())
        elif context.traceparent:
            carrier: Mapping[str, str] = {
                "traceparent": context.traceparent,
                **({"tracestate": context.tracestate} if context.tracestate else {}),
            }
            stack.enter_context(bus.with_trace(carrier=carrier))
        try:
            return bus(dto)
        except Exception:
            seen["trace_id"] = process.trace_ids().get("trace_id") or context.trace_id
            raise


def bus_call(
    bus: UseFramework, limiter: CapacityLimiter | None = None
) -> Callable[..., Awaitable[BusCall]]:
    """A FastAPI dependency answering `call`: `await call(dto)` runs `dto` on `bus` the way
    every generated route does. `limiter` bounds the worker threads (anyio's default, 40)."""

    async def provide(
        request: Request,
        credentials: Credentials = Depends(acting_credentials),
        context: RequestContext = Depends(request_context),
    ) -> BusCall:
        async def call(dto: Any) -> Any:
            setattr(request.state, BUSES_STATE, [bus])
            if context.idempotency_key is None and requires_idempotency_key(bus, type(dto)):
                raise IdempotencyKeyMissing(
                    f"{type(dto).__name__} runs once per request: send an Idempotency-Key "
                    "header"
                )
            seen: dict[str, str | None] = {}
            try:
                return await anyio.to_thread.run_sync(
                    partial(run_on_bus, bus, dto, credentials, context, seen),
                    limiter=limiter,
                )
            finally:
                if seen.get("trace_id"):
                    setattr(request.state, TRACE_STATE, seen["trace_id"])

        return call

    return provide
