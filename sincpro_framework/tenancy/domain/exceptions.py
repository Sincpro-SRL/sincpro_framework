"""What tenancy refuses — two causes, each answered by every wire with its own code."""

from sincpro_framework.exceptions import ClientError, FailureKind


class TenantRequired(ClientError):
    """The execution names no tenant and what it does needs one.

    Context: reading every tenant because none was named is the leak tenancy prevents; reading
    across them is asked by name (`without_tenant`)."""


class TenantNotAllowed(ClientError):
    """The caller names a tenant its memberships do not reach, or one that is not active."""

    failure_kind = FailureKind.PERMISSION_DENIED
