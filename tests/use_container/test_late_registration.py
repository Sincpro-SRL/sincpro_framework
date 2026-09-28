"""Something registered where it can no longer run is refused, not ignored.

A bus is fixed when it is built, and a repository reads its hooks when it is built. A Feature, a
dependency or a hook registered afterwards used to be accepted and then never used — the Feature
answered `UnknownDTOToExecute` although the container listed it. Each of them now says so at the
line that registered it.
"""

import importlib

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd import Entity, Hook, Hooks, MemoryRepository
from sincpro_framework.exceptions import BusAlreadyBuilt, ExtensionRefused


class CommandPing(DataTransferObject):
    pass


class CommandLate(DataTransferObject):
    pass


def _built() -> UseFramework:
    bus = UseFramework("late-registration", log_after_execution=False)

    @bus.feature(CommandPing)
    class Ping(Feature):
        def execute(self, dto: CommandPing) -> None:
            return None

    bus(CommandPing())
    return bus


def test_a_feature_registered_after_the_build_is_refused():
    bus = _built()

    with pytest.raises(BusAlreadyBuilt, match="CommandLate.*already built"):

        @bus.feature(CommandLate)
        class Late(Feature):
            def execute(self, dto: CommandLate) -> None:
                return None


def test_an_application_service_registered_after_the_build_is_refused():
    bus = _built()

    with pytest.raises(BusAlreadyBuilt, match="CommandLate.*already built"):

        @bus.app_service(CommandLate)
        class Late(ApplicationService):
            def execute(self, dto: CommandLate) -> None:
                return None


def test_a_dependency_added_after_the_build_is_refused():
    bus = _built()

    with pytest.raises(BusAlreadyBuilt, match="clock.*already built"):
        bus.add_dependency("clock", object())


def test_a_hook_decorated_after_a_repository_read_the_collection_is_refused():
    class Note(Entity):
        pass

    hooks = Hooks(None)
    MemoryRepository(hooks=hooks)  # reads the collection

    with pytest.raises(ExtensionRefused, match="LateAudit.*already read"):

        @hooks.on(Note)
        class LateAudit(Hook):
            def before_save(self, record: Note) -> None:
                return None


def test_loading_a_second_package_still_registers_its_hooks(tmp_path, monkeypatch):
    (tmp_path / "late_hooks_collection.py").write_text(
        "from sincpro_framework.ddd import Hooks\nhooks = Hooks(None)\n"
    )
    (tmp_path / "late_hooks_first.py").write_text("")
    (tmp_path / "late_hooks_second.py").write_text(
        "from sincpro_framework.ddd import Entity, Hook\n"
        "from late_hooks_collection import hooks\n"
        "class Note(Entity):\n    pass\n"
        "@hooks.on(Note)\nclass Audit(Hook):\n    def before_save(self, note): ...\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    hooks = importlib.import_module("late_hooks_collection").hooks

    hooks.load("late_hooks_first").load("late_hooks_second")

    assert [hook.__name__ for hook in hooks] == ["Audit"]


def test_everything_registered_before_the_build_still_works():
    bus = UseFramework("in-order", log_after_execution=False)
    bus.add_dependency("clock", object())

    @bus.feature(CommandPing)
    class Ping(Feature):
        def execute(self, dto: CommandPing) -> None:
            return None

    assert bus(CommandPing()) is None
