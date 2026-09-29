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

from sincpro_framework.auth.domain import AuthError
from sincpro_framework.auth.security_context import as_identity
from sincpro_framework.auth.transports import (
    credentials_from_asgi,
    http_status_of,
    refusal_body,
)

if TYPE_CHECKING:
    from sincpro_framework.auth.access_control import AccessControl

type Scope = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
type Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class IdentityMiddleware:
    def __init__(self, app: ASGIApp, access: "AccessControl[Any]") -> None:
        self.app = app
        self.access = access

    async def _refused(self, send: Send, error: AuthError) -> None:
        status = http_status_of(error)
        headers = [(b"content-type", b"application/json")]
        if status == 401:
            headers += [
                (b"www-authenticate", challenge.encode())
                for challenge in self.access.challenges()
            ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        body = json.dumps({"error": refusal_body(error)}).encode()
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        started = False

        async def sending(message: MutableMapping[str, Any]) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            credentials = credentials_from_asgi(scope, "http")
            identity = await asyncio.to_thread(self.access.authenticate, credentials)
            with as_identity(identity):
                await self.app(scope, receive, sending)
        except AuthError as error:
            if started or scope["type"] != "http":
                raise
            await self._refused(send, error)
