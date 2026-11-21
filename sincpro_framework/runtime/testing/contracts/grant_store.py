"""`GrantStoreContract`: what every `GrantStore` must do — a project keeping assignments its own
way proves it by inheriting this class.

    class TestMyGrants(GrantStoreContract):
        def make_store(self) -> GrantStore:
            return MyGrants(...)        new and empty for each test
"""

import threading
from datetime import UTC, datetime, timedelta

from sincpro_framework.auth.grants import Assignment, GrantStore


class GrantStoreContract:
    """Inherit, answer `make_store` with a new, empty store, and run with pytest."""

    racers = 16

    def make_store(self) -> GrantStore:
        """Answer a new, empty store."""
        raise NotImplementedError("answer a new, empty GrantStore")

    def test_what_is_assigned_is_held_and_only_there(self) -> None:
        """A role given in one tenant is held there, never in another nor by another subject."""
        store = self.make_store()
        store.assign(Assignment(subject="user:ana", tenant="bo", role="clerk"))
        assert store.held("user:ana", "bo").roles == {"clerk"}
        assert store.held("user:ana", "pe").roles == frozenset()
        assert store.held("user:bea", "bo").roles == frozenset()

    def test_a_role_taken_back_is_no_longer_held(self) -> None:
        """Unassigning leaves the subject's other roles; unassigning nothing is fine."""
        store = self.make_store()
        store.assign(Assignment(subject="user:ana", tenant="bo", role="clerk"))
        store.assign(Assignment(subject="user:ana", tenant="bo", role="auditor"))
        store.unassign("user:ana", "bo", "clerk")
        store.unassign("user:ana", "bo", "nobody-gave-it")
        assert store.held("user:ana", "bo").roles == {"auditor"}

    def test_an_assignment_past_its_end_is_kept_but_not_held(self) -> None:
        """A temporary role whose moment passed is still listed, never held."""
        store = self.make_store()
        past = datetime.now(UTC) - timedelta(minutes=1)
        store.assign(
            Assignment(subject="user:ana", tenant="bo", role="admin", valid_until=past)
        )
        assert store.held("user:ana", "bo").roles == frozenset()
        assert [one.role for one in store.assignments_of("user:ana", "bo")] == ["admin"]

    def test_who_holds_a_role_is_answered_per_tenant(self) -> None:
        """`holders` lists those given the role in the tenant."""
        store = self.make_store()
        for subject in ("user:ana", "user:bea"):
            store.assign(Assignment(subject=subject, tenant="bo", role="clerk"))
        store.assign(Assignment(subject="user:cid", tenant="pe", role="clerk"))
        assert [one.subject for one in store.holders("clerk", "bo")] == [
            "user:ana",
            "user:bea",
        ]

    def test_entitlements_are_per_tenant(self) -> None:
        """What a tenant is entitled to, given and withdrawn."""
        store = self.make_store()
        store.entitle("bo", "e-invoicing")
        store.entitle("bo", "payroll")
        store.withdraw("bo", "payroll")
        assert store.held("user:ana", "bo").entitlements == {"e-invoicing"}
        assert store.entitlements_of("pe") == frozenset()

    def test_racing_assignments_to_one_subject_are_all_kept(self) -> None:
        """`racers` roles given to one subject at once are all held — none lost."""
        store = self.make_store()
        barrier = threading.Barrier(self.racers)

        def give(index: int) -> None:
            barrier.wait()
            store.assign(Assignment(subject="user:ana", tenant="bo", role=f"role-{index}"))

        threads = [
            threading.Thread(target=give, args=(index,)) for index in range(self.racers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert store.held("user:ana", "bo").roles == {f"role-{i}" for i in range(self.racers)}
