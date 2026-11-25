"""Calling another API with a `TokenSource` — with any HTTP client, and with `requests` or
`httpx` as their own auth hooks: the headers added, a 401 answered by asking the source once and
sending again, never in a loop.

    answer = call_with_credentials(bank, OutgoingRequest(url=url, method="POST", body=body),
                                   lambda headers: http.post(url, content=body, headers=headers))
    requests.post(url, json=payload, auth=requests_auth(bank))
    httpx.Client(auth=httpx_auth(bank))

Context: facades — the call is `services/authenticated_call.py`, the hooks
`adapters/requests_auth.py` and `adapters/httpx_auth.py`. `requests` and `httpx` are not
dependencies of the framework: each is imported only when its hook is asked for.
"""

from collections.abc import Callable, Mapping
from typing import Any

from sincpro_framework.auth.client.domain.outbound import OutgoingRequest, TokenSource
from sincpro_framework.auth.client.services.authenticated_call import (
    response_headers,
    response_status,
    send_with_credentials,
)


def call_with_credentials[R](
    source: TokenSource,
    request: OutgoingRequest,
    send: Callable[[dict[str, str]], R],
    status_of: Callable[[R], int] = response_status,
    headers_of: Callable[[R], Mapping[str, str]] = response_headers,
) -> R:
    """`send` called with the source's headers; on a 401, once more with new ones — see
    `send_with_credentials`."""
    return send_with_credentials(source, request, send, status_of, headers_of)


def requests_auth(source: TokenSource, tenant: str | None = None) -> Any:
    """A `requests.auth.AuthBase` adding `source`'s headers, and sending once more on a 401."""
    from sincpro_framework.auth.client.adapters.requests_auth import RequestsTokenAuth

    return RequestsTokenAuth(source, tenant)


def httpx_auth(source: TokenSource, tenant: str | None = None) -> Any:
    """An `httpx.Auth` adding `source`'s headers, and sending once more on a 401 — for a client
    and an async client alike."""
    from sincpro_framework.auth.client.adapters.httpx_auth import HttpxTokenAuth

    return HttpxTokenAuth(source, tenant)
