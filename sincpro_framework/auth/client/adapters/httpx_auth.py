"""`HttpxTokenAuth`: a `TokenSource` as `httpx`' own auth flow — for a client and an async client
alike — its headers added, a 401 answered by asking the source once and sending again, never in a
loop. `httpx` is imported with this module, which the entrypoint loads only when `httpx_auth` is
asked for.

    httpx.Client(auth=httpx_auth(bank))
    httpx.AsyncClient(auth=httpx_auth(bank))       the source runs on a thread, off the loop

Context: new headers equal to those refused are not sent again — the same answer would come back.
"""

import asyncio
from collections.abc import AsyncGenerator, Generator
from typing import Any

import httpx

from sincpro_framework.auth.client.domain.outbound import OutgoingRequest, TokenSource
from sincpro_framework.auth.common.infrastructure.security_context import current_identity


class HttpxTokenAuth(httpx.Auth):
    """`source`'s headers on every request, for `tenant` when given."""

    def __init__(self, source: TokenSource, tenant: str | None = None) -> None:
        self.source = source
        self.tenant = tenant
        self.requires_request_body = source.needs_body

    def _build_outgoing_request(self, request: httpx.Request) -> OutgoingRequest:
        """The `OutgoingRequest` of an httpx request — its body only when the source reads it —
        bound to the caller in play."""
        outgoing = OutgoingRequest(
            url=str(request.url),
            method=str(request.method),
            body=request.content if self.source.needs_body else None,
            tenant=self.tenant,
        )
        return outgoing.bind_caller(current_identity())

    def auth_flow(self, request: httpx.Request) -> Generator[Any, Any, None]:
        """httpx's hook: the source's headers set; a 401 the source invalidates sends the
        request once more, with new headers — never the same ones again."""
        outgoing = self._build_outgoing_request(request)
        headers = self.source.headers_for(outgoing)
        request.headers.update(headers)
        response = yield request
        answer = {name.lower(): value for name, value in response.headers.items()}
        if response.status_code != 401 or not self.source.invalidate(
            outgoing, headers, answer
        ):
            return
        again = self.source.headers_for(outgoing)
        if again != headers:
            request.headers.update(again)
            yield request

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[Any, Any]:
        """The same, the source asked off the event loop."""
        outgoing = self._build_outgoing_request(request)
        headers = await asyncio.to_thread(self.source.headers_for, outgoing)
        request.headers.update(headers)
        response = yield request
        answer = {name.lower(): value for name, value in response.headers.items()}
        if response.status_code != 401 or not await asyncio.to_thread(
            self.source.invalidate, outgoing, headers, answer
        ):
            return
        again = await asyncio.to_thread(self.source.headers_for, outgoing)
        if again != headers:
            request.headers.update(again)
            yield request
