"""A call to another API sent with a `TokenSource`'s headers, and sent once more with new ones when
the API answers 401 — never in a loop.

    send_with_credentials(bank, OutgoingRequest(url=url, method="POST", body=body),
                          lambda headers: http.post(url, content=body, headers=headers))

Context: the tenant and the identity of the request are those in play when not given — so a
source per tenant answers the tenant the call acts for. A 403 is never retried: the token was
good, the API said no. `send` is called twice at most, so its body must be bytes, not a stream.
"""

from collections.abc import Callable, Mapping
from typing import Any

from sincpro_framework.auth.client.domain.outbound import OutgoingRequest, TokenSource
from sincpro_framework.auth.common.infrastructure.security_context import current_identity


def response_status(response: Any) -> int:
    """The status of a response of any client — `status_code`, `status` or `code`."""
    for name in ("status_code", "status", "code"):
        value = getattr(response, name, None)
        if isinstance(value, int):
            return value
    return 0


def response_headers(response: Any) -> dict[str, str]:
    """The headers of a response of any client, names lower-cased."""
    headers = getattr(response, "headers", None) or {}
    return {str(name).lower(): str(value) for name, value in dict(headers).items()}


def send_with_credentials[R](
    source: TokenSource,
    request: OutgoingRequest,
    send: Callable[[dict[str, str]], R],
    status_of: Callable[[R], int] = response_status,
    headers_of: Callable[[R], Mapping[str, str]] = response_headers,
) -> R:
    """`send` called with the source's headers; on a 401, once more with new ones.

    1. The headers for the request, the tenant and identity in play filled in; sent.
    2. Anything but a 401 is the answer — a 403 included.
    3. The source told the headers were refused; it says asking again cannot help: the 401.
    4. New headers asked; the same as before: the 401.
    Final: sent once more — its answer, whatever it is.
    """
    bound = request.bind_caller(current_identity())
    headers = source.headers_for(bound)
    response = send(dict(headers))
    if status_of(response) != 401:
        return response
    if not source.invalidate(bound, headers, headers_of(response)):
        return response
    again = source.headers_for(bound)
    if again == headers:
        return response
    return send(dict(again))
