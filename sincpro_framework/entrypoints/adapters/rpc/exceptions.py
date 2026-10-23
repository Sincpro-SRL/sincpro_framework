"""What the JSON-RPC wire raises; its codes are `errors`, the protocol's catalogue."""

from sincpro_framework.auth.domain.exceptions import AuthError
from sincpro_framework.ddd.exceptions import DuplicateAggregate
from sincpro_framework.exceptions import ClientError, FailureKind


class MethodNotFound(ClientError):
    def __init__(self, method: str):
        self.method = method
        super().__init__(method)


class InvalidParams(ClientError):
    """Params the method cannot take whatever their values — by position, on a by-name API."""

    def __init__(self, reason: str, message: str):
        self.reason = reason
        self.message = message
        super().__init__(message)
