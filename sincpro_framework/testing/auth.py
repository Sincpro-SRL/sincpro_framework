"""What a test needs from auth: be someone in one line, see what a provider was asked, and prove
a provider of the project's behaves like the ones shipped.

    with granting(BillingPermission.ISSUE_INVOICE):
        billing(CommandIssueInvoice(...))

    recording = RecordingProvider(OdooProvider(fake_odoo))
    ...
    assert ("has_permission", "user:1", "billing.invoice.issue") in recording.asked

    class TestOdooProvider(AuthProviderContract):
        def make_provider(self) -> AuthProvider: ...
        def accepted(self) -> Credentials: ...
        def foreign(self) -> Credentials: ...
        def rejected(self) -> Credentials: ...

Nothing here imports pytest.
"""

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from sincpro_framework.auth import (
    AuthProvider,
    Credentials,
    Identity,
    PermissionDenied,
    Unauthenticated,
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


class AuthProviderContract:
    """Inherit, answer the four methods, and run with pytest."""

    def make_provider(self) -> AuthProvider:
        raise NotImplementedError("answer the provider under test")

    def accepted(self) -> Credentials:
        raise NotImplementedError("answer credentials the provider authenticates")

    def foreign(self) -> Credentials:
        raise NotImplementedError("answer credentials of another kind — the provider's None")

    def rejected(self) -> Credentials:
        raise NotImplementedError("answer credentials of its kind that fail")

    def granted_permission(self) -> str | None:
        """A permission the accepted identity holds — `None` skips that check."""
        return None

    def test_accepted_credentials_are_someone(self) -> None:
        identity = self.make_provider().authenticate(self.accepted())
        if identity is None or identity.is_anonymous:
            raise AssertionError(f"the accepted credentials answered {identity!r}")

    def test_the_same_credentials_are_the_same_someone(self) -> None:
        provider = self.make_provider()
        first, second = provider.authenticate(self.accepted()), provider.authenticate(
            self.accepted()
        )
        if first is None or second is None or first.subject != second.subject:
            raise AssertionError("the same credentials answered two subjects")

    def test_foreign_credentials_are_not_its_kind(self) -> None:
        answered = self.make_provider().authenticate(self.foreign())
        if answered is not None:
            raise AssertionError(
                f"credentials of another kind answered {answered!r}: answer None, so the next "
                "provider is tried"
            )

    def test_rejected_credentials_raise_unauthenticated(self) -> None:
        try:
            self.make_provider().authenticate(self.rejected())
        except Unauthenticated:
            return
        except Exception as other:
            raise AssertionError(
                f"failing credentials raised {type(other).__name__}: raise Unauthenticated, "
                "the one every entrypoint answers 401 for"
            ) from other
        raise AssertionError("failing credentials were let through")

    def test_nobody_is_granted_nothing(self) -> None:
        provider = self.make_provider()
        try:
            granted = provider.has_permission(Identity.anonymous(), "any.permission")
        except PermissionDenied:
            return
        if granted:
            raise AssertionError("an anonymous identity was granted a permission")

    def test_what_the_identity_holds_is_granted(self) -> None:
        permission = self.granted_permission()
        if permission is None:
            return
        provider = self.make_provider()
        identity = provider.authenticate(self.accepted())
        assert identity is not None
        if not provider.has_permission(identity, permission):
            raise AssertionError(f"the accepted identity was refused {permission}")

    def test_a_batch_answers_one_decision_per_resource(self) -> None:
        provider = self.make_provider()
        identity = provider.authenticate(self.accepted())
        assert identity is not None
        resources: Sequence[Any] = (None, None, None)
        if len(provider.permitted(identity, "any.permission", resources)) != len(resources):
            raise AssertionError("permitted() answered a different number of decisions")
