"""What the REST wire raises."""

from sincpro_framework.auth.domain.exceptions import AuthError
from sincpro_framework.exceptions import ClientError, FailureKind


class InvalidRequest(ClientError):
    """A request the wire cannot read into a payload — answered 400."""
