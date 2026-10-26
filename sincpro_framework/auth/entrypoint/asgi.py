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
from collections.abc import Awaitable, Callable, MutableMapping
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.identity import Credentials
from sincpro_framework.auth.infrastructure.security_context import as_identity

if TYPE_CHECKING:
    from sincpro_framework.auth.entrypoint.access_control import AccessControl

type Scope = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
type Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class IdentityMiddleware:
    """ASGI middleware that authenticates each HTTP or WebSocket request and runs the app
    inside `as_identity` of who it turned out to be."""

    def __init__(self, app: ASGIApp, access: "AccessControl[Any]") -> None:
        """Wrap `app`, authenticating with `access` — the same `AccessControl` that guards the
        buses the app calls, so they agree on who may do what."""
        self.app = app
        self.access = access

    async def _refused(self, send: Send, error: exceptions.AuthError) -> None:
        """Answer the request with the refusal, as JSON — the body every transport answers.

        Unauthenticated("unknown token")  ->  401, www-authenticate: <each challenge>,
            {"error": {"kind": "unauthenticated", "reason": "unknown token"}}
        PermissionDenied(...)             ->  403, {"error": {"kind": "permission_denied",
            "reason": "lacks billing.invoice.issue", "requirement": "billing.invoice.issue"}}
        """
        status = error.http_status
        headers = [(b"content-type", b"application/json")]
        if status == 401:
            headers += [
                (b"www-authenticate", challenge.encode())
                for challenge in self.access.challenges()
            ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        body = json.dumps({"error": error.refusal_body()}).encode()
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the app for one connection as the identity its credentials stand for.

        1. A scope that is neither HTTP nor WebSocket (lifespan) passes through untouched.
        2. `send` is wrapped to note whether the response has started.
        3. Credentials are read from the scope and authenticated on a worker thread.
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
            credentials = Credentials.from_asgi(scope, "http")
            identity = await asyncio.to_thread(self.access.authenticate, credentials)
            with as_identity(identity):
                await self.app(scope, receive, sending)
        except exceptions.AuthError as error:
            if started or scope["type"] != "http":
                raise
            await self._refused(send, error)
