"""What a test needs from auth: be someone in one line, and see what a provider was asked.

    with granting(BillingPermission.ISSUE_INVOICE):
        billing(CommandIssueInvoice(...))

    recording = RecordingProvider(OdooProvider(fake_odoo))
    ...
    assert ("has_permission", "user:1", "billing.invoice.issue") in recording.asked

    signed_token({"iss": KEYCLOAK, "sub": "4c1d", "exp": …}, secret=SECRET, kid="2026-09")

Nothing here imports pytest.
"""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth import AuthProvider, Credentials, Identity, security
from sincpro_framework.auth.common.infrastructure import jws

if TYPE_CHECKING:
    from sincpro_framework.auth import PrivateKey


@contextmanager
def granting(
    *permissions: str, subject: str = "user:test", tenant: str | None = None
) -> Generator[Identity, None, None]:
    """Act as a user who holds exactly `permissions` — no provider, no bus, no token."""
    with security.as_identity(
        Identity.user(subject, tenant=tenant, permissions=permissions)
    ) as identity:
        yield identity


def signed_token(
    claims: Mapping[str, Any],
    key: "PrivateKey | None" = None,
    secret: str | None = None,
    kid: str | None = None,
) -> str:
    """A compact JWS of `claims` as an issuer would sign it — by its `key` (its algorithm and
    key id), or HS256 by a `secret` shared under `kid` (`SharedSecrets({kid: secret})`).

        signed_token({"iss": KEYCLOAK, "sub": "4c1d", "exp": now + 300}, key=signing)
        signed_token({"iss": "billing", "sub": "ana"}, secret=SECRET, kid="own")
    """
    if key is not None:
        header = {"alg": key.algorithm, "kid": key.key_id}
        return jws.encode({k: v for k, v in header.items() if v}, claims, key.sign)
    if secret is None or kid is None:
        raise ValueError("signed_token signs by a key, or by a secret under a kid")
    return jws.encode(
        {"alg": "HS256", "kid": kid}, claims, jws.HmacKey.from_secret(kid, secret).sign
    )


class RecordingProvider(AuthProvider):
    """A provider that records what it was asked — and answers what `inner` answers, or, with
    no `inner`, what the identity's permissions say."""

    def __init__(self, inner: AuthProvider | None = None, name: str | None = None) -> None:
        """Wrap `inner`, taking its `name` and `needs_body` so a chain treats the recording as
        the provider it wraps; with no `inner` it is named `"recording"`."""
        self.inner = inner
        self.name = name or (inner.name if inner is not None else "recording")
        self.needs_body = inner.needs_body if inner is not None else False
        self.asked: list[tuple[Any, ...]] = []

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """Record `("authenticate", transport)` — never the secret — and answer what `inner`
        answers; with no `inner`, `None`: these credentials are not its kind."""
        self.asked.append(("authenticate", credentials.transport))
        return self.inner.authenticate(credentials) if self.inner is not None else None

    def has_permission(
        self,
        identity: Identity,
        permission: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        """Record `("has_permission", subject, permission)` and answer what `inner` answers;
        with no `inner`, the port's default — the permissions the identity carries."""
        self.asked.append(("has_permission", identity.subject, permission))
        if self.inner is not None:
            return self.inner.has_permission(identity, permission, resource, context)
        return super().has_permission(identity, permission, resource, context)
