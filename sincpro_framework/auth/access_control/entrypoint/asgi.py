"""`IdentityMiddleware`: an ASGI app of the project's — FastAPI, Starlette, any other — acting as
whoever its requests' credentials say.

    app.add_middleware(IdentityMiddleware, access=auth)          # FastAPI / Starlette
    app = IdentityMiddleware(app, access=auth)                   # any ASGI app

Context: plain ASGI, so it imports no web library and runs under any of them. Every use case the
request runs — however many buses — acts as that identity. A refusal raised on the way is
answered as one: 401 with the providers' `WWW-Authenticate`, 403 with the reason — unless the
response had already started, when it is left to propagate. A provider may call the network, so
authenticating runs on a worker thread, never on the event loop. Lifespan and other scopes pass
through untouched.
"""

import asyncio
import json
import math
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Credentials
from sincpro_framework.auth.common.infrastructure.security_context import (
    as_identity,
    as_presented_token,
)
from sincpro_framework.common.failures import retry_after
from sincpro_framework.exceptions import ExternalServiceError, FailureKind

if TYPE_CHECKING:
    from sincpro_framework.auth.access_control.entrypoint.access_control import AccessControl

type Scope = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
type Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class IdentityMiddleware:
    """ASGI middleware that authenticates each HTTP or WebSocket request and runs the app
    inside `as_identity` of who it turned out to be."""

    def __init__(
        self, app: ASGIApp, access: "AccessControl[Any]", max_body_bytes: int = 1_048_576
    ) -> None:
        """Wrap `app`, authenticating with `access` — the same `AccessControl` that guards the
        buses the app calls, so they agree on who may do what. `max_body_bytes` bounds the body
        buffered for a provider that verifies it."""
        self.app = app
        self.access = access
        self.max_body_bytes = max_body_bytes

    async def _send_json(
        self,
        send: Send,
        status: int,
        error: dict[str, Any],
        headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        """Answer the request with `status` and `{"error": error}` as JSON — the body every
        transport answers a refusal with."""
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json"), *(headers or [])],
            }
        )
        body = json.dumps({"error": error}).encode()
        await send({"type": "http.response.body", "body": body})

    async def _refused(self, send: Send, error: exceptions.AuthError) -> None:
        """Answer the request with the refusal.

        Unauthenticated("unknown token")  ->  401, www-authenticate: <each challenge>,
            {"error": {"kind": "unauthenticated", "reason": "unknown token"}}
        PermissionDenied(...)             ->  403, {"error": {"kind": "permission_denied",
            "reason": "lacks billing.invoice.issue", "requirement": "billing.invoice.issue"}}
        """
        status = error.http_status
        challenges = (
            self.access.challenges(
                error if isinstance(error, exceptions.Unauthenticated) else None
            )
            if status == 401
            else []
        )
        headers = [(b"www-authenticate", challenge.encode()) for challenge in challenges]
        await self._send_json(send, status, error.refusal_body(), headers)

    async def _buffer_body(self, receive: Receive) -> tuple[bytes | None, Receive]:
        """The request's body read whole — `None` past `max_body_bytes` — and a `receive` that
        hands the app the same body again, so the app reads what the provider verified.

        Context: only when a provider reads bodies (a webhook signature); otherwise the app
        streams the body itself.
        """
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body_bytes:
                return None, receive
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        delivered = False

        async def replay() -> MutableMapping[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return body, replay

    async def _too_large(self, send: Send) -> None:
        """Answer 413: a body past `max_body_bytes` is never buffered."""
        await self._send_json(send, 413, {"kind": "invalid", "reason": "body too large"})

    async def _unavailable(self, send: Send, error: ExternalServiceError) -> None:
        """Answer that who calls could not be checked now — 503, to retry after what the error
        says (a second when it says nothing); never what the backend said.

            ServiceUnavailableError("registry timed out")  ->  503, retry-after: 1,
                {"error": {"kind": "unavailable", "reason": "who calls could not be checked…"}}
        """
        wait = retry_after(error, FailureKind.UNAVAILABLE) or timedelta(seconds=1)
        await self._send_json(
            send,
            503,
            {
                "kind": FailureKind.UNAVAILABLE.value,
                "reason": "who calls could not be checked now: retry",
            },
            [(b"retry-after", str(max(1, math.ceil(wait.total_seconds()))).encode())],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the app for one connection as the identity its credentials stand for.

        1. A scope that is neither HTTP nor WebSocket (lifespan) passes through untouched.
        2. `send` is wrapped to note whether the response has started.
        3. Credentials are read from the scope and authenticated on a worker thread, on the
           store of the bus the `AccessControl` guards, as every door authenticates — a
           provider whose backend did not answer (`ExternalServiceError`) is a 503 to retry,
           never a 401 nor a 500; a WebSocket lets it propagate.
        4. The app runs inside `as_identity(identity)`.
        5. An `AuthError` — from authenticating, or raised by a use case on the way:
           5.1 the response already started, or the scope is a WebSocket: it propagates;
           5.2 else it is answered as a 401/403 by `_refused`.
        Final: nothing — the app's own answer, or the refusal, went out through `send`.
        """
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        started = False

        async def sending(message: MutableMapping[str, Any]) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            if self.access.needs_body and scope["type"] == "http":
                body, receive = await self._buffer_body(receive)
                if body is None:
                    await self._too_large(send)
                    return
                credentials = Credentials.from_asgi(scope, "http", body=body)
            else:
                credentials = Credentials.from_asgi(scope, "http")
            try:
                identity = await asyncio.to_thread(self.access.authenticate, credentials)
            except ExternalServiceError as error:
                if scope["type"] != "http":
                    raise
                await self._unavailable(send, error)
                return
            with as_identity(identity), as_presented_token(credentials.bearer):
                await self.app(scope, receive, sending)
        except exceptions.AuthError as error:
            if started or scope["type"] != "http":
                raise
            await self._refused(send, error)
