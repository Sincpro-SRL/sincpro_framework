"""`RequestsTokenAuth`: a `TokenSource` as `requests`' own auth hook — its headers added, a 401
answered by asking the source once and sending again, never in a loop. `requests` is imported
with this module, which the entrypoint loads only when `requests_auth` is asked for.

    requests.post(url, json=payload, auth=requests_auth(bank))

Context: the retry is marked on the request being sent, never on this object, so one object
serves every thread — the shared mutable `AuthBase` that signed one merchant's request with
another's key is what this avoids. New headers equal to those refused are not sent again.
"""

from typing import Any

from requests.auth import AuthBase

from sincpro_framework.auth.client.domain.outbound import OutgoingRequest, TokenSource
from sincpro_framework.auth.common.infrastructure.security_context import current_identity

RETRY_MARKER = "_sincpro_auth_retried"
"""The attribute a request sent once more carries — so it is never sent a third time."""


class RequestsTokenAuth(AuthBase):
    """`source`'s headers on every request, for `tenant` when given."""

    def __init__(self, source: TokenSource, tenant: str | None = None) -> None:
        self.source = source
        self.tenant = tenant

    def _build_outgoing_request(self, prepared: Any) -> OutgoingRequest:
        """The `OutgoingRequest` of a prepared request — its body only when the source reads
        it — bound to the caller in play."""
        body = prepared.body.encode() if isinstance(prepared.body, str) else prepared.body
        request = OutgoingRequest(
            url=str(prepared.url),
            method=str(prepared.method),
            body=body if self.source.needs_body and isinstance(body, bytes) else None,
            tenant=self.tenant,
        )
        return request.bind_caller(current_identity())

    def _retry_on_unauthorized(self, response: Any, **options: Any) -> Any:
        """`requests`' response hook: a 401 the source invalidates, with new headers, sent once
        more on the same connection — the first answer stored in its history."""
        prepared = response.request
        if response.status_code != 401 or getattr(prepared, RETRY_MARKER, False):
            return response
        outgoing = self._build_outgoing_request(prepared)
        presented = {name.lower(): value for name, value in prepared.headers.items()}
        answer = {str(name).lower(): str(value) for name, value in response.headers.items()}
        if not self.source.invalidate(outgoing, presented, answer):
            return response
        headers = self.source.headers_for(outgoing)
        if all(presented.get(name.lower()) == value for name, value in headers.items()):
            return response
        again = prepared.copy()
        setattr(again, RETRY_MARKER, True)
        again.headers.update(headers)
        _ = response.content
        response.close()
        answered = response.connection.send(again, **options)
        answered.history.append(response)
        answered.request = again
        return answered

    def __call__(self, prepared: Any) -> Any:
        """The source's headers set on `prepared`; the retry registered as a response hook."""
        prepared.headers.update(
            self.source.headers_for(self._build_outgoing_request(prepared))
        )
        prepared.register_hook("response", self._retry_on_unauthorized)
        return prepared
