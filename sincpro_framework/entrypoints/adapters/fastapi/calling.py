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
from pydantic import ConfigDict, Field

from sincpro_framework.auth.domain.identity import Credentials
from sincpro_framework.auth.entrypoint.transports import (
    authenticated_as,
    credentials_from_asgi,
)
from sincpro_framework.context.adapters.propagation import extract
from sincpro_framework.context.domain.execution import CHAIN_KEYS, IDENTITY_HEADERS
from sincpro_framework.context.domain.level import EntrypointKind
from sincpro_framework.data_layer.caching import IDEMPOTENCY_KEY, declares_once
from sincpro_framework.data_layer.caching.domain.idempotent_command import IdempotentCommand
from sincpro_framework.entrypoints.adapters.fastapi import exceptions
from sincpro_framework.entrypoints.adapters.fastapi.problems import BUSES_STATE, TRACE_STATE
from sincpro_framework.observability import process
from sincpro_framework.observability.tracing.propagation import trace_id_of
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
    causation_id: str | None = None
    execution_id: str | None = None
    carried: dict[str, Any] = Field(default_factory=dict)
    """What the caller carried beside the identity — `baggage`, `sincpro-context` (what a
    frontend sends back)."""

    def bus_context(self) -> dict[str, Any]:
        """The keys the bus context opens with for this request — what was carried, the
        `Idempotency-Key` and the identity chain, each only when the request sent it.

        Context: the use case reads `idempotency_key` or `correlation_id` from its context,
        not from its DTO; an absent header writes no key, so the bus derives its own.

            RequestContext(idempotency_key="k-1", correlation_id="c-9",
                           carried={"tenant_id": "acme"}).bus_context()
            ->  {"tenant_id": "acme", "idempotency_key": "k-1", "correlation_id": "c-9"}

        1. What the caller carried (`baggage`, `sincpro-context`).
        2. The `Idempotency-Key`, under `idempotency_key`.
        3. Final: the identity chain — `execution_id`, `causation_id`, `correlation_id` — over
           what was carried.
        """
        found: dict[str, Any] = dict(self.carried)
        if self.idempotency_key:
            found[IDEMPOTENCY_KEY] = self.idempotency_key
        for key in CHAIN_KEYS:
            if getattr(self, key):
                found[key] = getattr(self, key)
        return found

    @property
    def trace_id(self) -> str | None:
        """The trace id the caller's `traceparent` names — `None` when it sent none or a
        malformed one; what a problem reports when the bus never opened a span."""
        return trace_id_of(self.traceparent)


async def acting_credentials(request: Request) -> Credentials:
    """Who the request says is calling — read, never checked: the bus's `AccessControl`
    authenticates them, in the thread that runs the use case. No I/O, so async."""
    return credentials_from_asgi(request.scope)


async def request_context(request: Request) -> RequestContext:
    """The `Idempotency-Key`, the W3C trace headers, the identity of the request and the context
    the caller carried (`baggage`, `sincpro-context`)."""
    headers = request.headers
    carried = extract(dict(headers))
    return RequestContext(
        idempotency_key=headers.get("idempotency-key") or None,
        traceparent=headers.get("traceparent") or None,
        tracestate=headers.get("tracestate") or None,
        **{key: headers.get(header) or None for header, key in IDENTITY_HEADERS.items()},
        carried={key: value for key, value in carried.items() if key not in CHAIN_KEYS},
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


def run_on_bus(
    bus: UseFramework,
    dto: Any,
    credentials: Credentials | None,
    context: RequestContext,
    seen: dict[str, str | None],
) -> Any:
    """Run `dto` on `bus` as the request's caller — in the worker thread `call` hands it to.

        run_on_bus(billing, CommandIssueInvoice(total=10), creds, context, seen)
        ->  ResponseIssueInvoice(...)
        run_on_bus(<bus raises>, dto, creds, context, seen)
        ->  the error raised; seen == {"trace_id": "4bf92f35..."}

    1. Authenticate `credentials` by the bus's `AccessControl` — in this thread.
    2. The request's context keys (`Idempotency-Key`, correlation id) into the bus context.
    3. The trace: the host's span when its instrumentation opened one, else the caller's
       `traceparent`; no second root.
    4. Final: `bus(dto)`; on failure, the trace it belongs to is kept in `seen`.
    """
    with ExitStack() as stack:
        stack.enter_context(authenticated_as(bus, credentials))
        extra = context.bus_context()
        if extra:
            stack.enter_context(bus.context(extra, kind=EntrypointKind.REST))
        if process.trace_ids():
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
    every generated route does. `limiter` bounds the worker threads (anyio's default, 40).

    Context: a hand-written route that called `bus(dto)` itself would run in the event loop,
    lose the identity to a thread copy and skip the problem's trace id; through `call` it is
    the generated route's path, component for component.

        call = Depends(bus_call(billing))         # in the route's signature
        await call(CommandIssueInvoice(total=10))  ->  the bus's answer, or its failure raised

    1. `provide`, the dependency FastAPI resolves per request: the acting credentials and
       the request's context, read once in the event loop.
    2. The `call` it answers, per DTO:
       2.1 the bus noted on the request state, so a 401 names its challenges;
       2.2 a use case that runs once, with no `Idempotency-Key` and a Command that does not
           identify itself — `IdempotencyKeyMissing`, before the bus;
       2.3 `run_on_bus` in a worker thread, bounded by `limiter`;
       2.4 on failure, the trace it belongs to kept on the request state.
    Final: `provide` — FastAPI calls it, the route gets `call`.
    """

    async def provide(
        request: Request,
        credentials: Credentials = Depends(acting_credentials),
        context: RequestContext = Depends(request_context),
    ) -> BusCall:
        """The dependency itself: reads the credentials and the request's context once, in
        the event loop, and answers the `call` that runs a DTO with them."""

        async def call(dto: Any) -> Any:
            """Run `dto` on the bus off the event loop, with this request's identity.

            1. The bus is noted on the request state — a 401 names its challenges.
            2. A use case that runs once, with no `Idempotency-Key` and a Command that does
               not identify itself, is refused before the bus (400).
            3. `run_on_bus` in a worker thread, bounded by `limiter`.
            4. Final: on failure, the trace it belongs to is kept on the request state, so the
               problem answered names it.
            """
            setattr(request.state, BUSES_STATE, [bus])
            if context.idempotency_key is None and requires_idempotency_key(bus, type(dto)):
                raise exceptions.IdempotencyKeyMissing(
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
