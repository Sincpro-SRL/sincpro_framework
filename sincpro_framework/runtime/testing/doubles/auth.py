"""What a test needs from auth: be someone in one line, and see what a provider was asked.

    with granting(BillingPermission.ISSUE_INVOICE):
        billing(CommandIssueInvoice(...))

    recording = RecordingProvider(OdooProvider(fake_odoo))
    ...
    assert ("has_permission", "user:1", "billing.invoice.issue") in recording.asked

Nothing here imports pytest.
"""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Any

from sincpro_framework.auth import (
    AuthProvider,
    Credentials,
    Identity,
    as_identity,
)


@contextmanager
def granting(
    *permissions: str, subject: str = "user:test", tenant: str | None = None
) -> Generator[Identity, None, None]:
    """Act as a user who holds exactly `permissions` — no provider, no bus, no token."""
    with as_identity(
        Identity.user(subject, tenant=tenant, permissions=permissions)
    ) as identity:
        yield identity


class RecordingProvider(AuthProvider):
    """A provider that keeps what it was asked — and answers what `inner` answers, or, with no
    `inner`, what the identity's permissions say."""

    def __init__(self, inner: AuthProvider | None = None, name: str | None = None) -> None:
        self.inner = inner
        self.name = name or (inner.name if inner is not None else "recording")
        self.needs_body = inner.needs_body if inner is not None else False
        self.asked: list[tuple[Any, ...]] = []

    def authenticate(self, credentials: Credentials) -> Identity | None:
        self.asked.append(("authenticate", credentials.transport))
        return self.inner.authenticate(credentials) if self.inner is not None else None

    def has_permission(
        self,
        identity: Identity,
        permission: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        self.asked.append(("has_permission", identity.subject, permission))
        if self.inner is not None:
            return self.inner.has_permission(identity, permission, resource, context)
        return super().has_permission(identity, permission, resource, context)
