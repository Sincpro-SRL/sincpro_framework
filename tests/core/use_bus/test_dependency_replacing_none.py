"""A dependency registered as `None` — an optional adapter with no key configured — is replaced
like any other: `replaces=None` names it."""

import pytest

from sincpro_framework import Feature, UseFramework, exceptions
from sincpro_framework.sincpro_abstractions import DataTransferObject


class CommandAsk(DataTransferObject):
    question: str


def assistant_bus() -> UseFramework:
    bus = UseFramework("assistant-none", log_after_execution=False)
    bus.add_dependency("language_model", None)  # no API key: no model

    @bus.feature(CommandAsk)
    class Ask(Feature):
        def execute(self, dto: CommandAsk) -> str:
            model = self.language_model
            return "no model" if model is None else model(dto.question)

    return bus


def test_a_none_registered_is_replaced_by_naming_it():
    test = assistant_bus().fresh()

    test.add_dependency("language_model", str.upper, replaces=None)

    assert test(CommandAsk(question="hi")) == "HI"


def test_a_name_taken_without_replaces_is_still_refused():
    bus = assistant_bus()

    with pytest.raises(exceptions.DependencyAlreadyRegistered):
        bus.add_dependency("language_model", str.upper)


def test_replacing_none_where_nothing_is_registered_works():
    bus = UseFramework("assistant-empty", log_after_execution=False)

    bus.add_dependency("language_model", str.upper, replaces=None)

    assert bus.deps.language_model is str.upper
