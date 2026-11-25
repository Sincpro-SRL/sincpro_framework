"""A token that leaks is of no use without what it is bound to: a DPoP key the client proves on
every request (RFC 9449), or the client certificate of an mTLS connection (RFC 8705); a SPIFFE
workload is its certificate; and what a shared store keeps of a secret is sealed."""

import hashlib
import time
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from sincpro_framework.auth import Credentials
from sincpro_framework.auth.client import (
    AesGcmSealer,
    ClientCredentials,
    DpopProver,
    HeldGrant,
    KeyValueHeldGrants,
    OutgoingRequest,
    TokenCache,
)
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.infrastructure import jws
from sincpro_framework.auth.server import (
    CertificateProvider,
    DpopProofs,
    JwtProvider,
    SharedSecrets,
    TrustedIssuer,
)
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.runtime.testing import FakeTokenEndpoint

SECRET = "a shared secret of thirty-two by!"
API = "https://api.colegio.bo"


def token(**claims) -> str:
    payload = {
        "iss": "colegio",
        "aud": "api",
        "sub": "ana",
        "exp": time.time() + 300,
        **claims,
    }
    return jws.encode({"alg": "HS256", "kid": "k"}, payload, jws.HmacKey.of("k", SECRET).sign)


def resource(**options) -> JwtProvider:
    return JwtProvider(
        TrustedIssuer("colegio", audience="api", keys=SharedSecrets({"k": SECRET})),
        dpop=DpopProofs(API, **options),
        realm="api",
    )


def dpop_request(
    access: str, prover: DpopProver, method: str = "GET", path: str = "/invoices"
) -> Credentials:
    return Credentials(
        transport="http",
        headers={
            "authorization": f"DPoP {access}",
            "dpop": prover.proof(method, f"{API}{path}", access),
        },
        method=method,
        uri=f"{path}?page=2",
    )


def test_a_bound_token_with_its_proof_is_let_through() -> None:
    prover = DpopProver()
    access = token(cnf={"jkt": prover.thumbprint})
    identity = resource().authenticate(dpop_request(access, prover))
    assert identity is not None and identity.subject == "user:ana"


@pytest.mark.parametrize(
    ("tamper", "reason"),
    [
        (
            lambda c, p, a: c.model_copy(
                update={"headers": {**c.headers, "authorization": f"Bearer {a}"}}
            ),
            "must be sent as DPoP",
        ),
        (lambda c, p, a: c.model_copy(update={"method": "POST"}), "another method"),
        (lambda c, p, a: c.model_copy(update={"uri": "/payments"}), "another URL"),
        (
            lambda c, p, a: c.model_copy(
                update={
                    "headers": {
                        **c.headers,
                        "dpop": DpopProver().proof("GET", f"{API}/invoices", a),
                    }
                }
            ),
            "another key",
        ),
        (
            lambda c, p, a: c.model_copy(
                update={
                    "headers": {
                        **c.headers,
                        "dpop": p.proof("GET", f"{API}/invoices", "other-token"),
                    }
                }
            ),
            "another token",
        ),
        (
            lambda c, p, a: c.model_copy(
                update={"headers": {k: v for k, v in c.headers.items() if k != "dpop"}}
            ),
            "exactly one proof",
        ),
    ],
)
def test_a_proof_for_anything_else_is_refused(tamper, reason: str) -> None:
    prover = DpopProver()
    access = token(cnf={"jkt": prover.thumbprint})
    with pytest.raises(exceptions.Unauthenticated, match=reason):
        resource().authenticate(tamper(dpop_request(access, prover), prover, access))


def test_a_proof_is_a_minute_long_and_used_once() -> None:
    prover = DpopProver(clock=lambda: time.time() - 120)
    access = token(cnf={"jkt": prover.thumbprint})
    with pytest.raises(exceptions.Unauthenticated, match="too old"):
        resource().authenticate(dpop_request(access, prover))
    fresh = DpopProver()
    access = token(cnf={"jkt": fresh.thumbprint})
    replayable = dpop_request(access, fresh)
    guarded = resource(replay=InMemoryKeyValue())
    assert guarded.authenticate(replayable) is not None
    with pytest.raises(exceptions.Unauthenticated, match="already used"):
        guarded.authenticate(replayable)


def test_required_proofs_refuse_a_plain_bearer_and_a_bound_token_needs_proofs_checked() -> (
    None
):
    plain = Credentials(transport="http", headers={"authorization": f"Bearer {token()}"})
    assert resource().authenticate(plain) is not None
    with pytest.raises(exceptions.Unauthenticated, match="must be sent as DPoP"):
        resource(required=True).authenticate(plain)
    unchecked = JwtProvider(
        TrustedIssuer("colegio", audience="api", keys=SharedSecrets({"k": SECRET}))
    )
    bound = Credentials(
        transport="http", headers={"authorization": f"Bearer {token(cnf={'jkt': 'x'})}"}
    )
    with pytest.raises(exceptions.Unauthenticated, match="no proofs are checked"):
        unchecked.authenticate(bound)
    assert 'DPoP algs="ES256 EdDSA PS256 RS256"' in (resource().challenge() or "")


def test_a_client_proves_its_key_for_the_token_and_for_each_request() -> None:
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    prover = DpopProver()
    bank = ClientCredentials(
        client_id="billing",
        client_secret="s3cret",
        token_endpoint=server.token_endpoint,
        http=server,
        cache=TokenCache(InMemoryKeyValue()),
        dpop=prover,
    )
    request = OutgoingRequest(url=f"{API}/qr?x=1", method="POST")
    headers = bank.headers_for(request)
    grant_proof = jws.parse(server.sent[-1].headers["dpop"])
    assert (grant_proof.claims["htm"], grant_proof.claims["htu"]) == (
        "POST",
        server.token_endpoint,
    )
    scheme, access = headers["authorization"].split()
    proof = jws.parse(headers["dpop"])
    assert scheme == "DPoP" and proof.claims["htu"] == f"{API}/qr"
    assert proof.claims["ath"] == jws.encoded(hashlib.sha256(access.encode()).digest())
    assert bank.invalidate(request, headers, {"dpop-nonce": "n-1"})
    assert jws.parse(bank.headers_for(request)["dpop"]).claims["nonce"] == "n-1"


def test_a_token_endpoint_demanding_a_nonce_is_asked_again_with_it() -> None:
    server = FakeTokenEndpoint(clients={"billing": "s3cret"}, dpop_nonce="n-7")
    prover = DpopProver()
    bank = ClientCredentials(
        client_id="billing",
        client_secret="s3cret",
        token_endpoint=server.token_endpoint,
        http=server,
        cache=TokenCache(InMemoryKeyValue()),
        dpop=prover,
    )
    headers = bank.headers_for(OutgoingRequest(url=f"{API}/qr", method="POST"))
    assert headers["authorization"].startswith("DPoP ")
    assert server.count(server.token_endpoint) == 2
    assert "nonce" not in jws.parse(server.sent[0].headers["dpop"]).claims
    assert jws.parse(server.sent[1].headers["dpop"]).claims["nonce"] == "n-7"


def certificate(
    name: str,
    spiffe: str | None = None,
    issuer=None,
    days: int = 30,
    start: datetime | None = None,
):
    key = ec.generate_private_key(ec.SECP256R1())
    signing_key, signing_name = (
        (issuer[1], issuer[0].subject)
        if issuer
        else (key, x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
    )
    begins = start or datetime.now(UTC) - timedelta(days=1)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(signing_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(begins)
        .not_valid_after(begins + timedelta(days=days))
    )
    if spiffe:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(spiffe)]),
            critical=False,
        )
    if issuer is None:
        builder = builder.add_extension(
            x509.BasicConstraints(ca=True, path_length=None), critical=True
        ).add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False),
            critical=True,
        )
    else:
        builder = (
            builder.add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer[0].public_key()),
                critical=False,
            )
        )
    if issuer is None:
        builder = builder.add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
    built = builder.sign(signing_key, hashes.SHA256())
    return built, key


def pem(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def over_mtls(cert, der: bool = False) -> Credentials:
    raw = cert.public_bytes(serialization.Encoding.DER) if der else pem(cert)
    return Credentials(transport="http", peer_certificate=raw)


CA = certificate("Sincpro CA")
WORKLOAD = certificate("billing", spiffe="spiffe://sincpro.bo/billing", issuer=CA)


def test_a_spiffe_workload_is_its_certificate_pem_or_der() -> None:
    provider = CertificateProvider(trust_domain="sincpro.bo", trusted_roots=[pem(CA[0])])
    for der in (False, True):
        identity = provider.authenticate(over_mtls(WORKLOAD[0], der))
        assert identity is not None and identity.subject == "service:billing"
        assert identity.token_id == jws.certificate_thumbprint(pem(WORKLOAD[0]))
    assert provider.authenticate(Credentials(transport="http")) is None


def test_a_certificate_of_another_domain_root_or_time_is_refused() -> None:
    with pytest.raises(exceptions.Unauthenticated, match="another trust domain"):
        CertificateProvider(trust_domain="elsewhere.bo").authenticate(over_mtls(WORKLOAD[0]))
    other_ca = certificate("Another CA")
    with pytest.raises(exceptions.Unauthenticated, match="not trusted"):
        CertificateProvider(trusted_roots=[pem(other_ca[0])]).authenticate(
            over_mtls(WORKLOAD[0])
        )
    expired = certificate(
        "old", issuer=CA, days=1, start=datetime.now(UTC) - timedelta(days=10)
    )
    with pytest.raises(exceptions.Unauthenticated, match="outside its validity"):
        CertificateProvider().authenticate(over_mtls(expired[0]))
    with pytest.raises(exceptions.Unauthenticated, match="not accepted"):
        CertificateProvider(
            subjects={"spiffe://sincpro.bo/orders": "service:orders"}
        ).authenticate(over_mtls(WORKLOAD[0]))


def test_a_token_bound_to_a_certificate_needs_that_certificate() -> None:
    bound = token(cnf={"x5t#S256": jws.certificate_thumbprint(pem(WORKLOAD[0]))})
    provider = JwtProvider(
        TrustedIssuer("colegio", audience="api", keys=SharedSecrets({"k": SECRET}))
    )
    over = lambda cert: Credentials(  # noqa: E731
        transport="http",
        headers={"authorization": f"Bearer {bound}"},
        peer_certificate=pem(cert),
    )
    assert provider.authenticate(over(WORKLOAD[0])) is not None
    with pytest.raises(exceptions.Unauthenticated, match="another client certificate"):
        provider.authenticate(over(CA[0]))


def test_what_a_shared_store_keeps_of_a_secret_is_sealed() -> None:
    sealer = AesGcmSealer(AesGcmSealer.new_key())
    sealed = sealer.seal(b"rt_secret")
    assert b"rt_secret" not in sealed and sealer.open(sealed) == b"rt_secret"
    with pytest.raises(ValueError):
        AesGcmSealer(AesGcmSealer.new_key()).open(sealed)
    store = InMemoryKeyValue()
    held = KeyValueHeldGrants(store, seal=sealer)
    assert held.replace("odoo", None, HeldGrant(refresh_token="rt_secret"))
    assert all(b"rt_secret" not in value for value, _ in store._values.values())
    found = held.current("odoo")
    assert found is not None and found.refresh_token == "rt_secret"
    server = FakeTokenEndpoint(clients={"billing": "s3cret"})
    cache_store = InMemoryKeyValue()
    bank = ClientCredentials(
        client_id="billing",
        client_secret="s3cret",
        token_endpoint=server.token_endpoint,
        http=server,
        cache=TokenCache(cache_store, seal=sealer),
    )
    assert bank.headers_for(OutgoingRequest(url=API)) == {"authorization": "Bearer at-1"}
    assert all(b"at-1" not in value for value, _ in cache_store._values.values())


def test_a_trust_domain_never_takes_a_common_name_for_a_spiffe_id() -> None:
    named_only = certificate("billing", issuer=CA)  # the proxy terminated TLS and checked it
    with pytest.raises(exceptions.Unauthenticated, match="no SPIFFE ID"):
        CertificateProvider(trust_domain="sincpro.bo").authenticate(over_mtls(named_only[0]))
    by_name = CertificateProvider().authenticate(over_mtls(named_only[0]))
    assert by_name is not None and by_name.subject == "service:billing"
