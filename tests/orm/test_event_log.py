"""Every event of a bounded context in one table: an aggregate's history is one read, written in
the same transaction as the change it records."""

from dataclasses import dataclass

import pytest
from sqlalchemy import Column, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd import DomainEvent, Entity, EventLogEntry
from sincpro_framework.ddd.exceptions import DuplicateAggregate
from sincpro_framework.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.orm.sqlalchemy.services.data_mapper import (
    entity_table,
    event_log_table,
    map_aggregates,
)

from .engines import fresh


@dataclass(kw_only=True)
class WorkspaceOpened(DomainEvent):
    name = "forge.workspace.v1.opened"
    code: str = ""


@dataclass(kw_only=True)
class RepositoryAttached(DomainEvent):
    name = "forge.workspace.v1.repository_attached"
    repository: str = ""


@dataclass
class Workspace(Entity):
    code: str = ""

    def open(self) -> None:
        self.record(WorkspaceOpened(code=self.code))

    def attach(self, repository: str) -> None:
        self.record(RepositoryAttached(repository=repository))


@dataclass(kw_only=True)
class WorkspaceHistory(EventLogEntry): ...


forge = registry()
workspace_table = entity_table("workspace", forge.metadata, Column("code", Text))
history_table = event_log_table("workspace_history", forge.metadata)


@pytest.fixture
def repository(engine_url: str) -> Repository:
    map_aggregates(forge, {Workspace: workspace_table, WorkspaceHistory: history_table})
    return Repository(fresh(engine_url, forge.metadata))


def test_an_aggregates_history_is_one_read_in_the_order_it_happened(repository):
    workspace = Workspace(code="sp-1")
    workspace.open()
    workspace.attach("api")
    with repository.context() as unit:
        unit.save(workspace)
        unit.save(WorkspaceHistory.of_all(workspace.pull_events()))
    other = Workspace(code="sp-2")
    other.open()
    repository.save([other, *WorkspaceHistory.of_all(other.pull_events())])

    history = repository.fetch_all(
        WorkspaceHistory, WorkspaceHistory.of_entity(workspace)
    ).items

    assert [entry.event_type for entry in history] == [
        "forge.workspace.v1.opened",
        "forge.workspace.v1.repository_attached",
    ]
    assert [entry.payload for entry in history] == [{"code": "sp-1"}, {"repository": "api"}]


def test_an_entry_keeps_the_events_envelope(repository):
    workspace = Workspace(code="sp-1")
    event = workspace.record(WorkspaceOpened(code="sp-1", correlation_id="req-7"))

    entry = WorkspaceHistory.of(event)

    assert (entry.id, entry.entity_type, entry.entity_id) == (
        event.id,
        "Workspace",
        workspace.id,
    )
    assert (entry.correlation_id, entry.sequence, entry.created_at) == (
        "req-7",
        0,
        event.created_at,
    )


def test_the_same_fact_saved_twice_is_a_duplicate_not_a_second_line(repository):
    workspace = Workspace(code="sp-1")
    repository.save(workspace)
    event = workspace.record(WorkspaceOpened(code="sp-1"))
    repository.save(WorkspaceHistory.of(event))

    with pytest.raises(DuplicateAggregate):
        repository.save(WorkspaceHistory.of(event))
