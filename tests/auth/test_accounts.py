"""A service that manages its own users: passwords hashed slow and salted, verified in constant
time, rehashed when the cost rises or the format is old; accounts locked after failures counted
atomically, the same for names nobody holds; HTTP Basic credentials checked once a minute."""

import base64
import threading
from collections.abc import Sequence
from datetime import timedelta

import pytest

from sincpro_framework.auth import (
    AuthProvider,
    BasicProvider,
    Credentials,
    CredentialVerifier,
    HashedCredentials,
    Identity,
    LoginAttempts,
    PasswordHasher,
    PasswordRejected,
    ScryptCost,
)
from sincpro_framework.auth.adapters.accounts import passwords
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.data_layer.store import InMemoryKeyValue, StoreUnavailable
from sincpro_framework.rate_limiter import RateLimited
from sincpro_framework.runtime.testing import AuthProviderContract

CHEAP = ScryptCost(ln=10)


def test_a_password_verifies_and_another_does_not() -> None:
    hasher = PasswordHasher(scheme="scrypt", scrypt=CHEAP)
    stored = hasher.hash("correct horse")
    assert stored.startswith("$scrypt$ln=10,r=8,p=1$")
    assert hasher.verify(stored, "correct horse") and not hasher.verify(stored, "wrong horse")
    assert hasher.hash("correct horse") != stored


def test_the_same_characters_typed_differently_are_one_password() -> None:
    hasher = PasswordHasher(scheme="scrypt", scrypt=CHEAP)
    assert hasher.verify(hasher.hash("ﬁle"), "file")


def test_nobody_learns_whether_a_username_exists() -> None:
    hasher = PasswordHasher(scheme="scrypt", scrypt=CHEAP)
    assert hasher.verify(None, "anything") is False


def test_a_password_too_long_is_refused_before_it_is_hashed() -> None:
    hasher = PasswordHasher(scheme="scrypt", scrypt=CHEAP, max_length=64)
    with pytest.raises(PasswordRejected):
        hasher.hash("x" * 65)
    assert not hasher.verify(hasher.hash("x" * 64), "x" * 65)


def test_a_cost_raised_or_a_scheme_changed_asks_for_a_new_hash() -> None:
    old = PasswordHasher(scheme="scrypt", scrypt=CHEAP).hash("pw")
    assert not PasswordHasher(scheme="scrypt", scrypt=CHEAP).needs_rehash(old)
    assert PasswordHasher(scheme="scrypt", scrypt=ScryptCost(ln=11)).needs_rehash(old)
    argon2 = PasswordHasher(scheme="argon2id")
    assert argon2.needs_rehash(old)
    assert argon2.verify(old, "pw")
    argon2_hash = argon2.hash("pw")
    assert argon2_hash.startswith("$argon2id$") and argon2.verify(argon2_hash, "pw")
    assert not argon2.needs_rehash(argon2_hash)


def quipu_clave(stored: str, password: str) -> bool:
    """quipu's `Clave` format: scrypt$N$r$p$salt$digest, in hex."""
    import hashlib
    import hmac

    _, n, r, p, salt, digest = stored.split("$")
    made = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32
    )
    return hmac.compare_digest(made.hex(), digest)


def test_a_legacy_hash_is_verified_and_asks_to_be_replaced() -> None:
    import hashlib

    salt = b"0123456789abcdef"
    digest = hashlib.scrypt(b"clave-123", salt=salt, n=2**10, r=8, p=1, dklen=32).hex()
    legacy = f"scrypt$1024$8$1${salt.hex()}${digest}"
    hasher = PasswordHasher(scheme="scrypt", scrypt=CHEAP, legacy={"scrypt$": quipu_clave})
    assert hasher.verify(legacy, "clave-123") and not hasher.verify(legacy, "otra")
    assert hasher.needs_rehash(legacy)


def test_argon2_without_its_library_is_said_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = PasswordHasher(scheme="argon2id").hash("pw")
    monkeypatch.setattr(passwords, "_argon2", lambda: None)
    with pytest.raises(exceptions.ProgrammingError, match=r"sincpro-framework\[argon2\]"):
        PasswordHasher(scheme="argon2id")
    auto = PasswordHasher(scrypt=CHEAP)
    assert auto.scheme == "scrypt"
    with pytest.raises(exceptions.ProgrammingError, match="argon2"):
        auto.verify(stored, "pw")


class Clock:
    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def __call__(self) -> float:
        return self.now


def test_an_account_locks_after_its_failures_and_opens_after_a_while() -> None:
    clock = Clock()
    attempts = LoginAttempts(
        InMemoryKeyValue(), max_failures=3, lock_for=timedelta(minutes=15), clock=clock
    )
    assert [attempts.failed("ana@acme.bo") for _ in range(3)] == [
        None,
        None,
        timedelta(minutes=15),
    ]
    with pytest.raises(RateLimited) as refused:
        attempts.refuse_if_locked("ANA@acme.bo ")
    assert refused.value.retry_after == timedelta(minutes=15)
    clock.now += 15 * 60
    attempts.refuse_if_locked("ana@acme.bo")


def test_a_success_starts_the_count_over_and_unknown_names_lock_alike() -> None:
    attempts = LoginAttempts(InMemoryKeyValue(), max_failures=2)
    attempts.failed("ana")
    attempts.succeeded("ana")
    assert attempts.failed("ana") is None
    assert [attempts.failed("nobody-has-this-name") for _ in range(2)][-1] is not None


def test_failures_at_once_are_all_counted_and_lock_once() -> None:
    attempts = LoginAttempts(InMemoryKeyValue(), max_failures=16)
    barrier = threading.Barrier(16)
    locked: list[timedelta | None] = []

    def fail() -> None:
        barrier.wait()
        locked.append(attempts.failed("ana"))

    threads = [threading.Thread(target=fail) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for one in locked if one is not None) == 1


class DownStore(InMemoryKeyValue):
    def increment(self, key: str, ttl: timedelta | None = None) -> int:
        raise StoreUnavailable("down")

    def get_many(self, keys: Sequence[str]) -> list[bytes | None]:
        raise StoreUnavailable("down")


def test_a_store_down_never_locks_everyone_out() -> None:
    attempts = LoginAttempts(DownStore(), max_failures=1)
    assert attempts.failed("ana") is None
    attempts.refuse_if_locked("ana")


class Counting(CredentialVerifier):
    def __init__(self, inner: CredentialVerifier) -> None:
        self.inner = inner
        self.asked = 0

    def verify(self, username: str, password: str) -> Identity | None:
        self.asked += 1
        return self.inner.verify(username, password)


HASHER = PasswordHasher(scheme="scrypt", scrypt=CHEAP)
ACCOUNTS = HashedCredentials(
    {
        "reports": (
            HASHER.hash("reports-secret"),
            Identity.service("reports", permissions={"a.b.c"}),
        )
    },
    HASHER,
)


def basic(username: str, password: str) -> Credentials:
    pair = base64.b64encode(f"{username}:{password}".encode()).decode()
    return Credentials(transport="http", headers={"authorization": f"Basic {pair}"})


def test_basic_credentials_are_hashed_once_a_minute() -> None:
    counting = Counting(ACCOUNTS)
    provider = BasicProvider(counting)
    for _ in range(3):
        identity = provider.authenticate(basic("reports", "reports-secret"))
        assert identity is not None and identity.provider == "basic"
    assert counting.asked == 1
    assert provider.challenge() == 'Basic realm="sincpro", charset="UTF-8"'


def test_basic_credentials_guessed_at_lock_the_username() -> None:
    provider = BasicProvider(
        ACCOUNTS, attempts=LoginAttempts(InMemoryKeyValue(), max_failures=2)
    )
    for _ in range(2):
        with pytest.raises(exceptions.Unauthenticated, match="wrong username or password"):
            provider.authenticate(basic("reports", "guess"))
    with pytest.raises(exceptions.Unauthenticated, match="too many failed attempts"):
        provider.authenticate(basic("reports", "reports-secret"))


def test_malformed_basic_credentials_are_refused_and_another_scheme_is_not_its_kind() -> None:
    provider = BasicProvider(ACCOUNTS)
    with pytest.raises(exceptions.Unauthenticated, match="malformed"):
        provider.authenticate(
            Credentials(transport="http", headers={"authorization": "Basic !!"})
        )
    with pytest.raises(exceptions.Unauthenticated, match="malformed"):
        provider.authenticate(
            Credentials(
                transport="http",
                headers={"authorization": f"Basic {base64.b64encode(b'nocolon').decode()}"},
            )
        )
    assert (
        provider.authenticate(
            Credentials(transport="http", headers={"authorization": "Bearer t"})
        )
        is None
    )


class TestBasicProviderContract(AuthProviderContract):
    def make_provider(self) -> AuthProvider:
        return BasicProvider(ACCOUNTS)

    def accepted(self) -> Credentials:
        return basic("reports", "reports-secret")

    def foreign(self) -> Credentials:
        return Credentials(transport="http", headers={"authorization": "Bearer t"})

    def rejected(self) -> Credentials:
        return basic("reports", "wrong")

    def granted_permission(self) -> str | None:
        return "a.b.c"
