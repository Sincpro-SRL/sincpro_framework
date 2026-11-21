"""`RefreshTokensContract`: what every `RefreshTokens` must do — a project keeping them its own
way proves it by inheriting this class.

    class TestMyRefreshTokens(RefreshTokensContract):
        def make_store(self) -> RefreshTokens:
            return MyRefreshTokens(...)        new and empty for each test
"""

import threading
from datetime import UTC, datetime, timedelta

from sincpro_framework.auth.accounts import RefreshRecord, RefreshTokens


def record(digest: str, family: str = "s-1", subject: str = "user:ana") -> RefreshRecord:
    """A live record of `family` — what a test saves and rotates."""
    now = datetime.now(UTC)
    return RefreshRecord(
        digest=digest,
        family=family,
        subject=subject,
        authenticated_at=now,
        issued_at=now,
        expires_at=now + timedelta(days=1),
        session_ends=now + timedelta(days=2),
    )


class RefreshTokensContract:
    """Inherit, answer `make_store` with a new, empty store, and run with pytest."""

    racers = 16

    def make_store(self) -> RefreshTokens:
        """Answer a new, empty store."""
        raise NotImplementedError("answer a new, empty RefreshTokens")

    def test_a_saved_token_is_found_by_its_digest(self) -> None:
        """A saved record is found by its digest, not rotated; an unknown digest is `None`."""
        store = self.make_store()
        store.save(record("d-1"))
        found = store.find("d-1")
        assert found is not None and found.family == "s-1" and found.rotated_at is None
        assert store.find("d-2") is None

    def test_rotating_marks_the_token_and_keeps_its_successor(self) -> None:
        """Rotating marks the token and keeps its successor; a token rotated already is never
        rotated again."""
        store = self.make_store()
        store.save(record("d-1"))
        at = datetime.now(UTC)
        assert store.rotate("d-1", record("d-2"), at)
        rotated, successor = store.find("d-1"), store.find("d-2")
        assert rotated is not None and rotated.rotated_at is not None
        assert successor is not None and successor.rotated_at is None
        assert not store.rotate("d-1", record("d-3"), at)
        assert store.find("d-3") is None

    def test_of_racing_rotations_exactly_one_wins(self) -> None:
        """`racers` threads rotating one token at once: exactly one wins."""
        store = self.make_store()
        store.save(record("d-1"))
        barrier = threading.Barrier(self.racers)
        won: list[bool] = []

        def rotate(index: int) -> None:
            barrier.wait()
            won.append(store.rotate("d-1", record(f"next-{index}"), datetime.now(UTC)))

        threads = [threading.Thread(target=rotate, args=(i,)) for i in range(self.racers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert won.count(True) == 1, won

    def test_the_sessions_of_a_subject_are_its_live_tokens(self) -> None:
        """The sessions of a subject are its tokens not rotated — another subject's are not
        among them."""
        store = self.make_store()
        store.save(record("a-1", family="s-a"))
        store.save(record("b-1", family="s-b"))
        store.save(record("c-1", family="s-c", subject="user:bea"))
        store.rotate("a-1", record("a-2", family="s-a"), datetime.now(UTC))
        assert sorted(one.digest for one in store.sessions_of("user:ana")) == ["a-2", "b-1"]
