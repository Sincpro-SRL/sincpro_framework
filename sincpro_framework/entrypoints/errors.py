"""What a driving adapter is allowed to tell a caller about a failure.

Shared by every wire (JSON-RPC, gRPC, later REST/CLI): the status code differs
per protocol, the disclosure policy does not.
"""

import json
from typing import Any

from pydantic import ValidationError

from sincpro_framework.ddd.exceptions import DomainError


def json_safe_validation_errors(error: ValidationError) -> list[dict[str, Any]]:
    """Pydantic puts the raw exception in ctx.error for value_error types (any
    ValueObject validate_fn that rejects input). json.dumps on that raises, so the
    error envelope would crash the host instead of returning an invalid-params status.
    """
    return json.loads(json.dumps(error.errors(), default=str))


def said_to_the_caller(error: Exception) -> str | None:
    """What of a failure a client is allowed to read.

    **A `DomainError` was written for whoever asked** — "an invoice has to balance" is the
    answer, and hiding it helps nobody. Anything else is the inside of the process, and its
    message routinely carries what must never leave it: a connection string with a password, a
    statement with the value it was filtering on, a path on the server.

        raise OperationalError("SELECT … WHERE token = 'secret'", …, "postgres://admin:hunter2@…")

    That whole string used to reach the client as the error's `data`. The log still gets all of
    it — the host's `logger.exception` runs either way — which is where it belongs.
    """
    return str(error) if isinstance(error, DomainError) else None
