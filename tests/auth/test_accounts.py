"""A service that manages its own users: passwords hashed slow and salted, verified in constant
time, rehashed when the cost rises or the format is old; accounts locked after failures counted
atomically, the same for names nobody holds; HTTP Basic credentials checked once a minute."""

import base64
import contextlib
import threading
import time
from collections.abc import Callable, Sequence
from datetime import timedelta

import pytest

from sincpro_framework.auth import AuthProvider, Credentials, Identity, PasswordRejected
from sincpro_framework.auth.accounts import (
    BasicProvider,
    CredentialVerifier,
    HashedCredentials,
    HashScheme,
    LoginAttempts,
    PasswordHasher,
    ScryptCost,
)
from sincpro_framework.auth.accounts.adapters.passwords import hasher
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.data_layer.store import InMemoryKeyValue, StoreUnavailable
from sincpro_framework.rate_limiter import RateLimited
from sincpro_framework.runtime.testing import AuthProviderContract

CHEAP = ScryptCost(ln=10)


def test_a_password_verifies_and_another_does_not() -> None:
    hasher = PasswordHasher(scheme=HashScheme.SCRYPT, scrypt=CHEAP)
    stored = hasher.hash("correct horse")
    assert stored.startswith("$scrypt$ln=10,r=8,p=1$")
    assert hasher.verify(stored, "correct horse") and not hasher.verify(stored, "wrong horse")
    assert hasher.hash("correct horse") != stored


def test_the_same_characters_typed_differently_are_one_password() -> None:
    hasher = PasswordHasher(scheme=HashScheme.SCRYPT, scrypt=CHEAP)
    assert hasher.verify(hasher.hash("ﬁle"), "file")


def test_nobody_learns_whether_a_username_exists() -> None:
    hasher = PasswordHasher(scheme=HashScheme.SCRYPT, scrypt=CHEAP)
    assert hasher.verify(None, "anything") is False


def test_a_password_too_long_is_refused_before_it_is_hashed() -> None:
    hasher = PasswordHasher(scheme=HashScheme.SCRYPT, scrypt=CHEAP, max_length=64)
    with pytest.raises(PasswordRejected):
        hasher.hash("x" * 65)
    assert not hasher.verify(hasher.hash("x" * 64), "x" * 65)


def test_a_cost_raised_or_a_scheme_changed_asks_for_a_new_hash() -> None:
    old = PasswordHasher(scheme=HashScheme.SCRYPT, scrypt=CHEAP).hash("pw")
    assert not PasswordHasher(scheme=HashScheme.SCRYPT, scrypt=CHEAP).needs_rehash(old)
    assert PasswordHasher(scheme=HashScheme.SCRYPT, scrypt=ScryptCost(ln=11)).needs_rehash(
        old
    )
    argon2 = PasswordHasher(scheme=HashScheme.ARGON2ID)
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
    hasher = PasswordHasher(
        scheme=HashScheme.SCRYPT, scrypt=CHEAP, legacy={"scrypt$": quipu_clave}
    )
    assert hasher.verify(legacy, "clave-123") and not hasher.verify(legacy, "otra")
    assert hasher.needs_rehash(legacy)


def test_argon2_without_its_library_is_said_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = PasswordHasher(scheme=HashScheme.ARGON2ID).hash("pw")
    monkeypatch.setattr(hasher, "_argon2", lambda: None)
    with pytest.raises(exceptions.ProgrammingError, match=r"sincpro-framework\[argon2\]"):
        PasswordHasher(scheme=HashScheme.ARGON2ID)
    auto = PasswordHasher(scrypt=CHEAP)
    assert auto.scheme == HashScheme.SCRYPT
    with pytest.raises(exceptions.ProgrammingError, match="argon2"):
        auto.verify(stored, "pw")


class Clock:
    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def __call__(self) -> float:
        return self.now


def fail(attempts: LoginAttempts, key: str) -> None:
    """One attempt at `key` whose password was wrong."""
    with pytest.raises(exceptions.Unauthenticated), attempts.attempt(key):
        raise exceptions.Unauthenticated("wrong username or password")


def test_an_account_locks_after_its_failures_and_opens_after_a_while() -> None:
    clock = Clock()
    attempts = LoginAttempts(
        InMemoryKeyValue(), max_failures=3, lock_for=timedelta(minutes=15), clock=clock
    )
    for _ in range(3):
        fail(attempts, "ana@acme.bo")
    assert attempts.locked_for("ANA@acme.bo ") == timedelta(minutes=15)
    with pytest.raises(RateLimited) as refused, attempts.attempt("ana@acme.bo"):
        pytest.fail("a locked account is never checked")
    assert refused.value.retry_after == timedelta(minutes=15)
    clock.now += 15 * 60
    with attempts.attempt("ana@acme.bo"):
        pass


def test_a_success_starts_the_count_over_and_unknown_names_lock_alike() -> None:
    attempts = LoginAttempts(InMemoryKeyValue(), max_failures=2)
    fail(attempts, "ana")
    with attempts.attempt("ana"):
        pass
    fail(attempts, "ana")
    assert attempts.locked_for("ana") is None
    for _ in range(2):
        fail(attempts, "nobody-has-this-name")
    assert attempts.locked_for("nobody-has-this-name") is not None


def at_once(count: int, attempt: Callable[[], None]) -> None:
    """`attempt` run by `count` threads released together."""
    barrier = threading.Barrier(count)

    def run() -> None:
        barrier.wait()
        attempt()

    threads = [threading.Thread(target=run) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def test_guesses_at_once_take_turns_and_never_pass_the_limit() -> None:
    attempts = LoginAttempts(InMemoryKeyValue(), max_failures=5)
    checked: list[int] = []

    def guess() -> None:
        with contextlib.suppress(RateLimited, exceptions.Unauthenticated):
            with attempts.attempt("ana"):
                checked.append(1)
                time.sleep(0.01)  # a hash's time: the others arrive while it runs
                raise exceptions.Unauthenticated("wrong username or password")

    at_once(50, guess)
    assert len(checked) == 5
    assert attempts.locked_for("ana") is not None


def test_the_right_password_sent_at_once_is_never_refused() -> None:
    attempts = LoginAttempts(InMemoryKeyValue(), max_failures=2)
    signed_in: list[int] = []

    def sign_in() -> None:
        with attempts.attempt("reports"):
            time.sleep(0.01)
            signed_in.append(1)

    at_once(10, sign_in)
    assert len(signed_in) == 10


def test_a_failure_that_is_not_a_wrong_password_counts_nothing() -> None:
    attempts = LoginAttempts(InMemoryKeyValue(), max_failures=1)
    for _ in range(3):
        with pytest.raises(StoreUnavailable), attempts.attempt("ana"):
            raise StoreUnavailable("the accounts table did not answer")
    assert attempts.locked_for("ana") is None


class DownStore(InMemoryKeyValue):
    def increment(self, key: str, ttl: timedelta | None = None) -> int:
        raise StoreUnavailable("down")

    def get_many(self, keys: Sequence[str]) -> list[bytes | None]:
        raise StoreUnavailable("down")


def test_a_store_down_never_locks_everyone_out() -> None:
    attempts = LoginAttempts(DownStore(), max_failures=1)
    fail(attempts, "ana")
    with attempts.attempt("ana"):
        pass


class Counting(CredentialVerifier):
    def __init__(self, inner: CredentialVerifier) -> None:
        self.inner = inner
        self.asked = 0

    def verify(self, username: str, password: str) -> Identity | None:
        self.asked += 1
        return self.inner.verify(username, password)


HASHER = PasswordHasher(scheme=HashScheme.SCRYPT, scrypt=CHEAP)
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
