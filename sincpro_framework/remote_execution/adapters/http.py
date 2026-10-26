"""The HTTP transport of hosted contexts — the standard library's client, no extra.

    POST /sincpro/contexts/execute        body: the message (`sincpro_framework.remote_execution.domain.payload`) —
                                          the DTO's identity, its values, the request context
    x-sp-bounded-context: billing         which context
    traceparent: 00-…                     the caller's trace

Context: the answer is a message too, rebuilt by the bus that called. Both
are held whole on each end, so a payload travels as long as it fits in memory there. One
connection per thread and address, held between calls — `http.client` connections are not shared
across threads. A connection left idle longer than `IDLE_REUSE` is replaced before
it is used, rather than risking one the server already closed: a call is never retried, because
a Command that already wrote would write twice.
"""

import base64
import http.client
import threading
import time
from collections.abc import Mapping
from typing import Any

from sincpro_framework.auth import security
from sincpro_framework.common.naming import registered_name
from sincpro_framework.observability.tracing.propagation import trace_carrier
from sincpro_framework.remote_execution.domain import exceptions
from sincpro_framework.remote_execution.domain.address import HostedAt, Scheme
from sincpro_framework.remote_execution.domain.failure_codec import FailureCodec
from sincpro_framework.remote_execution.domain.payload import Payload

PATH = "/sincpro/contexts/execute"
CONTEXT_HEADER = "x-sp-bounded-context"
ERROR_MODULE_HEADER = "x-sp-error-module"
ERROR_KIND_HEADER = "x-sp-error-kind"
ERROR_DETAILS_HEADER = "x-sp-error-details"
IDLE_REUSE = 2.0
"""Seconds a held connection may sit idle and still be used — below any server's keep-alive."""


class _Held(threading.local):
    """The connection this thread holds for one address, and when it last answered — per
    thread, because an `http.client` connection is not safe to share."""

    connection: http.client.HTTPConnection | None = None
    last_used: float = 0.0


class HttpTransport:
    """The caller's side for one address."""

    def __init__(self, hosted_at: HostedAt) -> None:
        """Nothing connects here: each thread opens its own connection at its first call."""
        self.hosted_at = hosted_at
        self._held = _Held()

    def _connection(self) -> http.client.HTTPConnection:
        """This thread's connection to the address, fit to write on.

        1. Held but idle past `IDLE_REUSE`: closed and dropped — the server may have closed
           it, and a request lost on it could not be retried.
        Final: the held one, else a new one — HTTPS when the address says so — with the
        address's deadline as its socket timeout.
        """
        held = self._held
        stale = time.monotonic() - held.last_used > IDLE_REUSE
        if held.connection is not None and stale:
            held.connection.close()
            held.connection = None
        if held.connection is None:
            opener = (
                http.client.HTTPSConnection
                if self.hosted_at.scheme is Scheme.HTTPS
                else http.client.HTTPConnection
            )
            held.connection = opener(self.hosted_at.address, timeout=self.hosted_at.timeout)
        return held.connection

    def _forget(self) -> None:
        """This thread's connection closed and dropped — after any failure on it, so the next
        call never writes on a connection in an unknown state."""
        if self._held.connection is not None:
            self._held.connection.close()
        self._held.connection = None

    def _sent(
        self, body: bytes, headers: Mapping[str, str], where: str
    ) -> http.client.HTTPConnection:
        """The request written whole on a connection — a host never runs a DTO it did not read
        to the end, so a failure here is `ContextUnavailable`: it did not run."""
        try:
            connection = self._connection()
            connection.request("POST", PATH, body=body, headers=dict(headers))
            return connection
        except (OSError, http.client.HTTPException) as error:
            self._forget()
            raise exceptions.ContextUnavailable(
                f"{where} is not answering: {error}", unreached=True
            ) from None
        except BaseException:
            self._forget()
            raise

    def _answered(self, connection: http.client.HTTPConnection, where: str) -> Payload:
        """The answer, once the request was sent — a failure from here on may follow a run:
        `ContextTimeout` past the deadline, `ContextOutcomeUnknown` when the connection is lost.

        1. 200: the answer's message.
        2. 404: the service there does not host the context.
        3. Final: the error the host raised, as itself.
        """
        try:
            answer = connection.getresponse()
            body = answer.read()
            self._held.last_used = time.monotonic()
            if answer.status == 200:
                return Payload.from_json(body)
        except TimeoutError:
            self._forget()
            raise exceptions.ContextTimeout(
                f"{where} did not answer within {self.hosted_at.timeout:g}s", unreached=True
            ) from None
        except (OSError, http.client.HTTPException) as error:
            self._forget()
            raise exceptions.ContextOutcomeUnknown(
                f"{where} took the call and was lost: {error}", unreached=True
            ) from None
        except BaseException:
            self._forget()
            raise
        message = body.decode(errors="replace")
        if answer.status == 404:
            raise exceptions.ContextUnavailable(f"{where}: {message}", unreached=True)
        details = answer.getheader(ERROR_DETAILS_HEADER)
        raise FailureCodec.rebuilt(
            answer.getheader(ERROR_MODULE_HEADER),
            answer.getheader(ERROR_KIND_HEADER),
            message,
            base64.b64decode(details) if details else None,
        )

    def execute(self, context: str, dto: Any, request: Mapping[str, Any]) -> Payload:
        """Run `dto` on the context hosted at the address, over one HTTP POST.

            transport.execute("billing", CommandPay(amount=10), {"correlation_id": "c1"})
            ->  Payload(type="billing.ResponsePay", data={"paid": True}, ...)
            <answered 404>      ->  ContextUnavailable("billing at http://billing:8000: ...")

        1. The message as the body — the DTO by its identity, its values, the request
           context; the context, the trace and who the call acts for as headers.
        2. One POST, each read and write within the address's deadline.
        Final: the answer's message, or the failure raised as itself.
        """
        headers = {
            "content-type": "application/json",
            CONTEXT_HEADER: context,
            **trace_carrier(),
            **security.identity_headers(context),
        }
        body = Payload.of(registered_name(type(dto), context), dto, request).as_json()
        where = f"{context} at {self.hosted_at.address}"
        return self._answered(self._sent(body, headers, where), where)
