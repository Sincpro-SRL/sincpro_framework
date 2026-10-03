"""The HTTP transport of hosted contexts — the standard library's client, no extra.

    POST /sincpro/contexts/execute        body: the DTO's values (`domain.payload`), chunked
    x-sp-bounded-context: billing         which context
    x-sp-dto: my_erp.billing.CommandX     which DTO, by its registered name
    x-sp-request-context: <base64>        the caller's request context, packed
    traceparent: 00-…                     the caller's trace

Context: the body goes out as chunks, and the answer is read from the socket straight into the
unpickler — no payload is ever one buffer, so several GiB travel as long as they fit in memory on
both ends. One connection per thread and address, kept between calls — `http.client` connections
are not shared across threads. A connection left idle longer than `IDLE_REUSE` is replaced before
it is used, rather than risking one the server already closed: a call is never retried, because
a Command that already wrote would write twice.
"""

import base64
import http.client
import threading
import time
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from sincpro_framework.ioc import registered_name
from sincpro_framework.remote_execution.domain.errors import (
    ContextOutcomeUnknown,
    ContextTimeout,
    ContextUnavailable,
    DTODoesNotFit,
    raised_as_itself,
)
from sincpro_framework.remote_execution.domain.payload import pack_context, packed, unpacked
from sincpro_framework.transport.addresses import HostedAt, Wire

PATH = "/sincpro/contexts/execute"
CONTEXT_HEADER = "x-sp-bounded-context"
DTO_HEADER = "x-sp-dto"
REQUEST_CONTEXT_HEADER = "x-sp-request-context"
ERROR_MODULE_HEADER = "x-sp-error-module"
ERROR_KIND_HEADER = "x-sp-error-kind"
ERROR_DETAILS_HEADER = "x-sp-error-details"
IDLE_REUSE = 2.0
"""Seconds a kept connection may sit idle and still be used — below any server's keep-alive."""


class _Kept(threading.local):
    connection: http.client.HTTPConnection | None = None
    last_used: float = 0.0


class HttpTransport:
    """The caller's side for one address."""

    def __init__(self, hosted_at: HostedAt) -> None:
        self.hosted_at = hosted_at
        self._kept = _Kept()

    def _connection(self) -> http.client.HTTPConnection:
        kept = self._kept
        stale = time.monotonic() - kept.last_used > IDLE_REUSE
        if kept.connection is not None and stale:
            kept.connection.close()
            kept.connection = None
        if kept.connection is None:
            opener = (
                http.client.HTTPSConnection
                if self.hosted_at.wire is Wire.HTTPS
                else http.client.HTTPConnection
            )
            kept.connection = opener(self.hosted_at.address, timeout=self.hosted_at.timeout)
        return kept.connection

    def _forget(self) -> None:
        if self._kept.connection is not None:
            self._kept.connection.close()
        self._kept.connection = None

    def _sent(
        self, dto: Any, headers: Mapping[str, str], where: str
    ) -> http.client.HTTPConnection:
        """The request written whole on a connection — a host never runs a DTO it did not read
        to the end, so a failure here is `ContextUnavailable`: it did not run."""
        try:
            connection = self._connection()
            connection.request(
                "POST", PATH, body=packed(dto), headers=dict(headers), encode_chunked=True
            )
            return connection
        except (OSError, http.client.HTTPException) as error:
            self._forget()
            raise ContextUnavailable(f"{where} is not answering: {error}") from None
        except BaseException:
            self._forget()
            raise

    def _answered(
        self, connection: http.client.HTTPConnection, response: Any, where: str
    ) -> Any:
        """The answer, once the request was sent — a failure from here on may follow a run:
        `ContextTimeout` past the deadline, `ContextOutcomeUnknown` when the connection is lost.

        1. 200: the values rebuilt as `response` as they are read.
        2. 404: the service there does not host the context.
        3. Final: the error the host raised, as itself.
        """
        try:
            answer = connection.getresponse()
            if answer.status == 200:
                rebuilt = unpacked(answer, response)
                answer.read()
                self._kept.last_used = time.monotonic()
                return rebuilt
            body = answer.read()
            self._kept.last_used = time.monotonic()
        except TimeoutError:
            self._forget()
            raise ContextTimeout(
                f"{where} did not answer within {self.hosted_at.timeout:g}s"
            ) from None
        except (OSError, http.client.HTTPException) as error:
            self._forget()
            raise ContextOutcomeUnknown(
                f"{where} took the call and was lost: {error}"
            ) from None
        except ValidationError as error:
            self._forget()
            raise DTODoesNotFit(f"the answer of {where}", error) from None
        except BaseException:
            self._forget()
            raise
        message = body.decode(errors="replace")
        if answer.status == 404:
            raise ContextUnavailable(f"{where}: {message}")
        details = answer.getheader(ERROR_DETAILS_HEADER)
        raise raised_as_itself(
            answer.getheader(ERROR_MODULE_HEADER),
            answer.getheader(ERROR_KIND_HEADER),
            message,
            base64.b64decode(details) if details else None,
        )

    def execute(
        self, context: str, dto: Any, response: Any, request: Mapping[str, Any]
    ) -> Any:
        """1. The DTO's values as a chunked body; the context, the DTO's name, the request
           context, the trace and who the call acts for as headers.
        2. One POST, each read and write within the address's deadline.
        3. Final: the answer rebuilt as `response` — as the host sent it when `None` — or the
           failure raised as itself.
        """
        from sincpro_framework.auth.transports import identity_headers
        from sincpro_framework.event_driven.infrastructure.trace import trace_carrier

        headers = {
            "content-type": "application/octet-stream",
            CONTEXT_HEADER: context,
            DTO_HEADER: registered_name(type(dto)),
            REQUEST_CONTEXT_HEADER: base64.b64encode(pack_context(request)).decode(),
            **trace_carrier(),
            **identity_headers(context),
        }
        where = f"{context} at {self.hosted_at.address}"
        return self._answered(self._sent(dto, headers, where), response, where)
