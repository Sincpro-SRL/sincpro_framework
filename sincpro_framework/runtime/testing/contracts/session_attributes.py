"""`SessionAttributesContract`: what every `SessionAttributes` must do — a project keeping what
sessions keep its own way proves it by inheriting this class.

    class TestMySessionAttributes(SessionAttributesContract):
        def make_store(self) -> SessionAttributes:
            return MySessionAttributes(...)        new and empty for each test
"""

from datetime import timedelta

from sincpro_framework.auth.accounts import SessionAttributes


class SessionAttributesContract:
    """Inherit, answer `make_store` with a new, empty store, and run with pytest."""

    def make_store(self) -> SessionAttributes:
        """Answer a new, empty store."""
        raise NotImplementedError("answer a new, empty SessionAttributes")

    def test_what_a_session_saved_is_found_as_it_was(self) -> None:
        """Saved values come back equal — JSON text, numbers, lists and mappings."""
        store = self.make_store()
        values = {"credential": "cr_1", "agents": ["sales", "finance"], "quota": {"left": 3}}
        store.save("sid_1", values, timedelta(minutes=5))
        assert store.find("sid_1") == values
        assert store.find("sid_2") is None

    def test_saving_again_replaces_what_it_kept(self) -> None:
        """A second save replaces the values — never merges them."""
        store = self.make_store()
        store.save("sid_1", {"a": 1, "b": 2}, timedelta(minutes=5))
        store.save("sid_1", {"a": 3}, timedelta(minutes=5))
        assert store.find("sid_1") == {"a": 3}

    def test_a_deleted_session_keeps_nothing(self) -> None:
        """Deleted, nothing is found; deleting what kept nothing is not an error."""
        store = self.make_store()
        store.save("sid_1", {"a": 1}, timedelta(minutes=5))
        store.delete("sid_1")
        store.delete("sid_never")
        assert store.find("sid_1") is None

    def test_sessions_are_kept_apart(self) -> None:
        """One session's values are never another's."""
        store = self.make_store()
        store.save("sid_1", {"who": "ana"}, timedelta(minutes=5))
        store.save("sid_2", {"who": "luis"}, timedelta(minutes=5))
        assert store.find("sid_1") == {"who": "ana"}
        assert store.find("sid_2") == {"who": "luis"}
