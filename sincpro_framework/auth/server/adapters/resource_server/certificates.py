"""`CertificateProvider`: the caller is the client certificate of its mutual-TLS connection — a
service of a mesh, a SPIFFE workload, a bank's integration — the `[jwt]` extra (`cryptography`).

    CertificateProvider(trust_domain="sincpro.bo")              spiffe://sincpro.bo/billing
    CertificateProvider(trusted_roots=[partner_ca_pem], subjects={"bank-bnb": "service:bnb"})

Context: the TLS server already checked the chain when it terminates mTLS itself; behind a proxy
that only forwards the certificate, `trusted_roots` checks it here. A certificate outside its
validity, a SPIFFE ID of another trust domain, or one this provider was not told about when
`subjects` lists them, is `Unauthenticated`. The certificate's SHA-256 thumbprint is the
identity's `token_id` — what a certificate-bound token (RFC 8705) names.
"""

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sincpro_framework.auth.common.adapters.jose_keys import JWT_MISSING
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Credentials, Identity, IdentityKind
from sincpro_framework.auth.common.domain.provider import AuthProvider
from sincpro_framework.auth.common.domain.resolver import IdentityResolver, resolved
from sincpro_framework.auth.common.infrastructure import jws

try:  # pragma: no cover - tests/core/test_core_without_extras.py
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.x509.verification import PolicyBuilder, Store, VerificationError
except ImportError as error:  # pragma: no cover
    raise ImportError(JWT_MISSING) from error


class CertificateProvider(AuthProvider):
    """Callers by the client certificate they presented."""

    def __init__(
        self,
        trust_domain: str | None = None,
        subjects: Mapping[str, str] | None = None,
        trusted_roots: Sequence[bytes] | None = None,
        permissions: Iterable[str] = (),
        resolvers: Sequence[IdentityResolver] = (),
        name: str = "mtls",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`subjects` maps a SPIFFE ID or a common name to the subject it stands for — only
        those are accepted when given; `trusted_roots` (PEM) check the chain here."""
        self.trust_domain = trust_domain
        self.subjects = dict(subjects) if subjects is not None else None
        self._store = (
            Store([x509.load_pem_x509_certificate(one) for one in trusted_roots])
            if trusted_roots
            else None
        )
        self.permissions = frozenset(permissions)
        self.resolvers = tuple(resolvers)
        self.name = name
        self.clock = clock

    def security_scheme(self) -> dict[str, Any] | None:
        """Mutual TLS — the client's certificate is its credential."""
        return {"type": "mutualTLS"}

    def _load_certificate(self, raw: bytes) -> Any:
        """The certificate, PEM or DER — `Unauthenticated` when it is neither."""
        try:
            return x509.load_der_x509_certificate(jws.to_der(raw))
        except ValueError as error:
            raise exceptions.Unauthenticated("unreadable client certificate") from error

    def _subject_of(self, certificate: Any) -> tuple[str, bool]:
        """The certificate's name — its SPIFFE ID when it has one — and whether it is one."""
        try:
            uris = certificate.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.UniformResourceIdentifier)
        except x509.ExtensionNotFound:
            uris = []
        spiffe = [one for one in uris if one.startswith("spiffe://")]
        if len(spiffe) > 1:
            raise exceptions.Unauthenticated("a client certificate names several SPIFFE IDs")
        if spiffe:
            return spiffe[0], True
        names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if not names:
            raise exceptions.Unauthenticated("a client certificate names nobody")
        return str(names[0].value), False

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """The service the client certificate names.

        1. No certificate: `None`.
        2. Unreadable, outside its validity, or not chained to `trusted_roots`: `Unauthenticated`.
        3. With a `trust_domain`: no SPIFFE ID, or one of another trust domain — a common name
           alone never stands in for it. A name `subjects` does not list.
        Final: a SERVICE identity — the subject `subjects` maps it to, else `service:<path or
        common name>` — with the certificate's thumbprint and expiry.
        """
        raw = credentials.peer_certificate
        if raw is None:
            return None
        certificate = self._load_certificate(raw)
        now = datetime.fromtimestamp(self.clock(), UTC)
        if not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc:
            raise exceptions.Unauthenticated("client certificate outside its validity")
        if self._store is not None:
            verifier = PolicyBuilder().store(self._store).time(now).build_client_verifier()
            try:
                verifier.verify(certificate, [])
            except VerificationError as error:
                raise exceptions.Unauthenticated("client certificate not trusted") from error
        named, is_spiffe = self._subject_of(certificate)
        if self.trust_domain is not None:
            if not is_spiffe:
                raise exceptions.Unauthenticated(
                    f"no SPIFFE ID: only spiffe://{self.trust_domain}/ is accepted"
                )
            if not named.startswith(f"spiffe://{self.trust_domain}/"):
                raise exceptions.Unauthenticated("SPIFFE ID of another trust domain")
        if self.subjects is not None:
            if named not in self.subjects:
                raise exceptions.Unauthenticated("client certificate not accepted")
            subject = self.subjects[named]
        else:
            subject = "service:" + (named.split("/", 3)[-1] if is_spiffe else named)
        identity = Identity(
            subject=subject,
            kind=IdentityKind.SERVICE,
            permissions=self.permissions,
            provider=self.name,
            token_id=jws.certificate_thumbprint(raw),
            expires_at=certificate.not_valid_after_utc,
        )
        return resolved(identity, self.resolvers)
