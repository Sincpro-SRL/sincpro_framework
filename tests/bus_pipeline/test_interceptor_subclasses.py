"""An interceptor for a base Command wraps its subclasses when it says so — the base itself need
not be answered by any handler."""

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework, exceptions


class WriteCommand(DataTransferObject):
    company_id: int = 0


class CommandCreateLead(WriteCommand):
    name: str


class CommandCreateQuote(WriteCommand):
    total: int


class QueryLeads(DataTransferObject):
    pass


def crm(seen: list[str], subclasses: bool) -> UseFramework:
    bus = UseFramework(f"crm-subclasses-{subclasses}", log_after_execution=False)

    for command in (CommandCreateLead, CommandCreateQuote, QueryLeads):

        @bus.feature(command)
        class Handler(Feature):
            def execute(self, dto) -> None:
                return None

    @bus.interceptor(WriteCommand, subclasses=subclasses)
    def every_write(dto, call_next):
        seen.append(type(dto).__name__)
        return call_next(dto)

    return bus


def test_every_subclass_of_the_base_is_wrapped_and_nothing_else():
    seen: list[str] = []
    bus = crm(seen, subclasses=True)

    bus(CommandCreateLead(name="Juan"))
    bus(CommandCreateQuote(total=10))
    bus(QueryLeads())

    assert seen == ["CommandCreateLead", "CommandCreateQuote"]


def test_without_it_the_base_alone_is_named_and_must_be_answered():
    bus = crm([], subclasses=False)

    with pytest.raises(exceptions.UnknownDTOToExecute, match="WriteCommand"):
        bus.build_root_bus()
