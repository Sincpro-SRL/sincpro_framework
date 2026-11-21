"""A link that works once, for what it was sent for, the older ones void when a newer one is sent;
and a second factor whose codes are RFC 6238's own, accepted once each."""

import base64
import threading
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from sincpro_framework.auth.accounts import OneTimeTokens, Totp
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.data_layer.store import InMemoryKeyValue

RFC6238 = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


def links() -> OneTimeTokens:
    return OneTimeTokens(InMemoryKeyValue())


def test_a_link_works_once_for_what_it_was_sent_for() -> None:
    tokens = links()
    token = tokens.issue(
        "password-reset", "user:ana", ttl=timedelta(minutes=30), data={"to": "ana@x"}
    )
    assert token.startswith("ot_")
    peeked = tokens.peek("password-reset", token)
    assert peeked is not None and peeked.subject == "user:ana"
    with pytest.raises(exceptions.Unauthenticated):
        tokens.redeem("invitation", token)
    grant = tokens.redeem("password-reset", token)
    assert (grant.subject, grant.data) == ("user:ana", {"to": "ana@x"})
    with pytest.raises(exceptions.Unauthenticated, match="already used"):
        tokens.redeem("password-reset", token)


def test_only_the_last_link_sent_works() -> None:
    tokens = links()
    first = tokens.issue("password-reset", "user:ana", ttl=timedelta(minutes=30))
    second = tokens.issue("password-reset", "user:ana", ttl=timedelta(minutes=30))
    with pytest.raises(exceptions.Unauthenticated, match="newer link"):
        tokens.redeem("password-reset", first)
    assert tokens.redeem("password-reset", second).subject == "user:ana"
    third = tokens.issue("password-reset", "user:ana", ttl=timedelta(minutes=30))
    tokens.void("password-reset", "user:ana")
    with pytest.raises(exceptions.Unauthenticated):
        tokens.redeem("password-reset", third)


def test_two_clicks_at_once_redeem_a_link_once() -> None:
    tokens = links()
    token = tokens.issue(
        "invitation", "user:bea", ttl=timedelta(days=7), single_outstanding=False
    )
    barrier = threading.Barrier(16)
    redeemed: list[bool] = []

    def click() -> None:
        barrier.wait()
        try:
            tokens.redeem("invitation", token)
            redeemed.append(True)
        except exceptions.Unauthenticated:
            redeemed.append(False)

    threads = [threading.Thread(target=click) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert redeemed.count(True) == 1


def test_no_link_is_stored_as_issued() -> None:
    store = InMemoryKeyValue()
    token = OneTimeTokens(store).issue("password-reset", "user:ana", ttl=timedelta(minutes=5))
    assert token not in " ".join(store._values) + " ".join(
        str(v) for v, _ in store._values.values()
    )


@pytest.mark.parametrize(("moment", "code"), RFC6238)
def test_codes_are_rfc_6238s_own(moment: int, code: str) -> None:
    secret = base64.b32encode(b"12345678901234567890").decode()
    totp = Totp("x", digits=8, clock=lambda: moment)
    assert totp.now(secret) == code
    assert totp.verify(secret, code)


def test_a_code_is_accepted_within_one_step_either_side_and_never_beyond() -> None:
    now = [1_900_000_000.0]
    totp = Totp("CAUOR", clock=lambda: now[0])
    secret = totp.new_secret()
    code = totp.now(secret)
    now[0] += 30
    assert totp.verify(secret, code)
    now[0] += 30
    assert not totp.verify(secret, code)
    assert not totp.verify(secret, "12345") and not totp.verify(secret, "abcdef")
    assert not totp.verify(secret, "\u0661\u0662\u0663\u0664\u0665\u0666")  # not ASCII


def test_a_code_is_accepted_once_and_never_an_older_one() -> None:
    now = [1_900_000_000.0]
    totp = Totp("CAUOR", replay=InMemoryKeyValue(), clock=lambda: now[0])
    secret = totp.new_secret()
    code = totp.now(secret)
    assert totp.verify(secret, code, subject="user:ana")
    assert not totp.verify(secret, code, subject="user:ana")
    assert totp.verify(secret, code, subject="user:bea")
    older = totp.code_at(secret, int(now[0] // 30) - 1)
    assert not totp.verify(secret, older, subject="user:ana")


def test_the_qr_code_says_what_the_app_needs() -> None:
    totp = Totp("Colegio de Auditores")
    uri = totp.provisioning_uri("JBSWY3DPEHPK3PXP", "MFAQ0123")
    parts = urlsplit(uri)
    assert (parts.scheme, parts.netloc) == ("otpauth", "totp")
    assert parse_qs(parts.query) == {
        "secret": ["JBSWY3DPEHPK3PXP"],
        "issuer": ["Colegio de Auditores"],
        "algorithm": ["SHA1"],
        "digits": ["6"],
        "period": ["30"],
    }
