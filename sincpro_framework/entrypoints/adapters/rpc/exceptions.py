"""What the JSON-RPC wire raises; its codes are `errors`, the protocol's catalogue."""

from sincpro_framework.auth import AuthError
from sincpro_framework.ddd.exceptions import DuplicateAggregate
from sincpro_framework.exceptions import ClientError, FailureKind


class MethodNotFound(ClientError):
    """A request names a method the surface does not publish — answered `-32601`, the method
    named in `data`."""

    def __init__(self, method: str):
        """`method` is the name the request asked for, stored for the answer's `data`."""
        self.method = method
        super().__init__(method)


class InvalidParams(ClientError):
    """Params the method cannot take whatever their values — by position, on a by-name API."""

    def __init__(self, reason: str, message: str):
        """`reason` is the UPPER_SNAKE word a client switches on (`PARAMS_BY_POSITION`);
        `message` the text a person reads."""
        self.reason = reason
        self.message = message
        super().__init__(message)
