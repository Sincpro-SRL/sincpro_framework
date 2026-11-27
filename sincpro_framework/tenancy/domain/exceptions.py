"""What tenancy refuses, each told to the caller with the failure every wire already answers."""

from sincpro_framework.exceptions import ClientError, FailureKind


class TenantRequired(ClientError):
    """The execution names no tenant, and what it does needs one — a read or a write of data
    partitioned by tenant, or a use case declared `@tenancy.required`.

    Context: reading every tenant's data because none was named is the leak tenancy exists to
    prevent; reading across tenants is asked for by name (`without_tenant`)."""


class TenantNotChosen(TenantRequired):
    """The caller belongs to several tenants and named none of them — which one is the caller's
    to say (a header, a claim, a selector), never the server's to guess."""


class TenantNotAllowed(ClientError):
    """The caller names a tenant its memberships do not reach, or one that is not active."""

    failure_kind = FailureKind.PERMISSION_DENIED
