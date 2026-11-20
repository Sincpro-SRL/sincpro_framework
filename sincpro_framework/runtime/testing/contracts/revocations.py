"""`RevocationsContract`: what every `Revocations` must do — a project storing them its own way
proves it by inheriting this class.

    class TestMyRevocations(RevocationsContract):
        def make_revocations(self) -> Revocations:
            return MyRevocations(...)        new and empty for each test
"""

import threading
from datetime import UTC, datetime, timedelta

from sincpro_framework.auth import Revocations, remembering_versions

HOUR = timedelta(hours=1)


class RevocationsContract:
    """Inherit, answer `make_revocations` with new, empty revocations, and run with pytest."""

    racers = 16

    def make_revocations(self) -> Revocations:
        """Answer new, empty revocations — each test revokes from scratch."""
        raise NotImplementedError("answer new, empty revocations")

    def _later(self) -> datetime:
        """An hour from now — long enough for every entry a test writes to outlive it."""
        return datetime.now(UTC) + HOUR

    def test_nothing_is_revoked_at_first(self) -> None:
        """A subject never touched has a clean state and version 0."""
        state = self.make_revocations().state("user:1", token_id="t-1", session="s-1")
        assert state.refusal(datetime.now(UTC)) is None, state
        assert state.permission_version == 0

    def test_a_revoked_token_is_refused_and_no_other(self) -> None:
        """Revoking one token id refuses it, never another of the same subject."""
        revocations = self.make_revocations()
        revocations.revoke_token("t-1", self._later())
        assert revocations.state("user:1", token_id="t-1").refusal(None) == "token revoked"
        assert revocations.state("user:1", token_id="t-2").refusal(datetime.now(UTC)) is None

    def test_a_revoked_session_refuses_every_credential_of_it(self) -> None:
        """A logout refuses every credential of the session, never another session."""
        revocations = self.make_revocations()
        revocations.revoke_session("s-1", self._later())
        assert revocations.state("user:1", session="s-1").session_revoked
        assert not revocations.state("user:1", session="s-2").session_revoked

    def test_signing_out_everywhere_refuses_what_was_issued_before(self) -> None:
        """A credential issued before the subject signed out everywhere is refused; one issued
        after, or one of another subject, is not."""
        revocations = self.make_revocations()
        before = datetime.now(UTC) - timedelta(minutes=1)
        revocations.revoke_subject("user:1", self._later())
        after = datetime.now(UTC) + timedelta(seconds=1)
        assert revocations.state("user:1").refusal(before) == "signed out"
        assert revocations.state("user:1").refusal(after) is None
        assert revocations.state("user:2").refusal(before) is None

    def test_a_moment_already_past_revokes_nothing(self) -> None:
        """Revoking until a moment already gone stores nothing — nothing it covers is alive."""
        revocations = self.make_revocations()
        revocations.revoke_token("t-1", datetime.now(UTC) - HOUR)
        assert not revocations.state("user:1", token_id="t-1").token_revoked

    def test_racing_bumps_each_answer_a_new_version(self) -> None:
        """`racers` threads bumping one subject at once each get a version of their own."""
        revocations = self.make_revocations()
        barrier = threading.Barrier(self.racers)
        answers: list[int] = []
        lock = threading.Lock()

        def bump() -> None:
            barrier.wait()
            version = revocations.bump_permissions("user:1")
            with lock:
                answers.append(version)

        threads = [threading.Thread(target=bump) for _ in range(self.racers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(answers) == list(range(1, self.racers + 1)), answers
        assert revocations.permission_version("user:1") == self.racers
        assert revocations.permission_version("user:2") == 0

    def test_related_versions_are_read_with_the_state_and_remembered(self) -> None:
        """`state(related=…)` answers the related subjects' versions with its own; inside
        `remembering_versions()` they are answered again without another read."""
        revocations = self.make_revocations()
        revocations.bump_permissions("user:1")
        revocations.bump_permissions("tenant:bo")
        revocations.bump_permissions("tenant:bo")
        state = revocations.state("user:1", related=["tenant:bo", "tenant:pe"])
        assert state.permission_version == 1
        assert state.versions == {"tenant:bo": 2, "tenant:pe": 0}
        with remembering_versions():
            revocations.state("user:1", related=["tenant:bo"])
            revocations.bump_permissions("tenant:bo")
            assert revocations.permission_versions("user:1", "tenant:bo") == {
                "user:1": 1,
                "tenant:bo": 2,
            }
        assert revocations.permission_version("tenant:bo") == 3
