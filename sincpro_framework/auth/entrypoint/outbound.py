"""Calling another API with a `TokenSource` — with any HTTP client, and with `requests` or
`httpx` as their own auth hooks: the headers added, a 401 answered by asking the source once and
sending again, never in a loop.

    answer = call_with_credentials(bank, OutgoingRequest(url=url, method="POST", body=body),
                                   lambda headers: http.post(url, content=body, headers=headers))
    requests.post(url, json=payload, auth=requests_auth(bank))
    httpx.Client(auth=httpx_auth(bank))

Context: the tenant and the identity of the request are those in play when not given — so a
source per tenant answers the tenant the call acts for. A 403 is never retried: the token was
good, the API said no. `send` is called twice at most, so its body must be bytes, not a stream.
`requests` and `httpx` are imported only when their hook is asked for.
"""

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator, Mapping
from typing import Any

from sincpro_framework.auth.domain.outbound import OutgoingRequest, TokenSource
from sincpro_framework.auth.infrastructure.security_context import current_identity

RETRIED = "_sincpro_auth_retried"


def in_play(request: OutgoingRequest) -> OutgoingRequest:
    """`request` with the tenant and the identity of the execution in play, when it names none."""
    identity = request.identity or current_identity()
    return request.model_copy(
        update={"identity": identity, "tenant": request.tenant or identity.tenant}
    )


def _status_of(response: Any) -> int:
    """The status of a response of any client — `status_code`, `status` or `code`."""
    for name in ("status_code", "status", "code"):
        value = getattr(response, name, None)
        if isinstance(value, int):
            return value
    return 0


def _headers_of(response: Any) -> dict[str, str]:
    """The headers of a response of any client, names lower-cased."""
    headers = getattr(response, "headers", None) or {}
    return {str(name).lower(): str(value) for name, value in dict(headers).items()}


def call_with_credentials[R](
    source: TokenSource,
    request: OutgoingRequest,
    send: Callable[[dict[str, str]], R],
    status_of: Callable[[R], int] = _status_of,
    headers_of: Callable[[R], Mapping[str, str]] = _headers_of,
) -> R:
    """`send` called with the source's headers; on a 401, once more with new ones.

    1. The headers for the request, the tenant and identity in play filled in; sent.
    2. Anything but a 401 is the answer — a 403 included.
    3. The source told the headers were refused; it says asking again cannot help: the 401.
    4. New headers asked; the same as before: the 401.
    Final: sent once more — its answer, whatever it is.
    """
    asked = in_play(request)
    headers = source.headers_for(asked)
    response = send(dict(headers))
    if status_of(response) != 401:
        return response
    if not source.invalidate(asked, headers, headers_of(response)):
        return response
    again = source.headers_for(asked)
    if again == headers:
        return response
    return send(dict(again))


def _request_of(url: Any, method: Any, body: Any, needs_body: bool) -> OutgoingRequest:
    """An `OutgoingRequest` out of a library's request — the body only when it is read."""
    raw = body.encode() if isinstance(body, str) else body
    return in_play(
        OutgoingRequest(
            url=str(url),
            method=str(method),
            body=raw if needs_body and isinstance(raw, bytes) else None,
        )
    )


def requests_auth(source: TokenSource, tenant: str | None = None) -> Any:
    """A `requests.auth.AuthBase` adding `source`'s headers, and sending once more on a 401.

    Context: the retry is marked on the request being sent, never on this object, so one
    object serves every thread — the shared mutable `AuthBase` that signed one merchant's
    request with another's key is what this avoids.
    """
    from requests.auth import AuthBase

    class SourceAuth(AuthBase):
        def _asked(self, prepared: Any) -> OutgoingRequest:
            asked = _request_of(
                prepared.url, prepared.method, prepared.body, source.needs_body
            )
            return asked.model_copy(update={"tenant": tenant or asked.tenant})

        def __call__(self, prepared: Any) -> Any:
            prepared.headers.update(source.headers_for(self._asked(prepared)))
            prepared.register_hook("response", self._retried)
            return prepared

        def _retried(self, response: Any, **options: Any) -> Any:
            prepared = response.request
            if response.status_code != 401 or getattr(prepared, RETRIED, False):
                return response
            asked = self._asked(prepared)
            presented = {name.lower(): value for name, value in prepared.headers.items()}
            if not source.invalidate(asked, presented, _headers_of(response)):
                return response
            again = prepared.copy()
            setattr(again, RETRIED, True)
            again.headers.update(source.headers_for(asked))
            _ = response.content
            response.close()
            answered = response.connection.send(again, **options)
            answered.history.append(response)
            answered.request = again
            return answered

    return SourceAuth()


def httpx_auth(source: TokenSource, tenant: str | None = None) -> Any:
    """An `httpx.Auth` adding `source`'s headers, and sending once more on a 401 — for a client
    and an async client alike: the source runs on a thread, never on the event loop."""
    import httpx

    class SourceAuth(httpx.Auth):
        requires_request_body = source.needs_body

        def _asked(self, request: Any) -> OutgoingRequest:
            body = request.content if source.needs_body else None
            asked = _request_of(request.url, request.method, body, source.needs_body)
            return asked.model_copy(update={"tenant": tenant or asked.tenant})

        def auth_flow(self, request: Any) -> Generator[Any, Any, None]:
            asked = self._asked(request)
            headers = source.headers_for(asked)
            request.headers.update(headers)
            response = yield request
            if response.status_code == 401 and source.invalidate(
                asked, headers, _headers_of(response)
            ):
                request.headers.update(source.headers_for(asked))
                yield request

        async def async_auth_flow(self, request: Any) -> AsyncGenerator[Any, Any]:
            asked = self._asked(request)
            headers = await asyncio.to_thread(source.headers_for, asked)
            request.headers.update(headers)
            response = yield request
            if response.status_code == 401 and await asyncio.to_thread(
                source.invalidate, asked, headers, _headers_of(response)
            ):
                request.headers.update(await asyncio.to_thread(source.headers_for, asked))
                yield request

    return SourceAuth()
