"""A record's history, read like its other reads: `DomainEvents[Issue, R]` on the record's own
`EntityReads`, filtered by the record's type and identity together, oldest first.

The cases are a project context: issues and runs record events into one event class per
context, a sandbox and a workspace share the id `dev-1`, and an account is asked for by its
code rather than its identity.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from sincpro_framework import ProgrammingError, UseFramework
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    AggregateNotFound,
    Condition,
    Criteria,
    DomainEvent,
    DomainEvents,
    Entity,
    EntityReads,
    Get,
    ResponsePaginatedQuery,
    ResponseRecord,
    history_of,
)
from sincpro_framework.ddd.criteria import Operator, parse_order


@dataclass(kw_only=True)
class ProjectEvent(DomainEvent):
    pass


@dataclass(kw_only=True)
class IssueOpened(ProjectEvent):
    title: str = ""


@dataclass(kw_only=True)
class IssueClosed(ProjectEvent):
    resolution: str = ""


@dataclass(kw_only=True)
class RunStarted(ProjectEvent):
    pass


@dataclass(kw_only=True)
class SandboxReserved(ProjectEvent):
    pass


@dataclass(kw_only=True)
class WorkspaceCreated(ProjectEvent):
    pass


@dataclass
class Issue(Entity):
    title: str = ""

    def open(self) -> None:
        self.record(IssueOpened(title=self.title))

    def close(self, resolution: str) -> None:
        self.record(IssueClosed(resolution=resolution))


@dataclass
class Run(Entity):
    issue_id: str = ""

    def start(self) -> None:
        self.record(RunStarted())


@dataclass
class Sandbox(Entity):
    def reserve(self) -> None:
        self.record(SandboxReserved())


@dataclass
class Workspace(Entity):
    def create(self) -> None:
        self.record(WorkspaceCreated())


@dataclass
class Account(Entity):
    code: str = ""

    @classmethod
    def DEFAULT_GET_ID(cls) -> str:
        return "code"

    def open(self) -> None:
        self.record(IssueOpened(title=self.code))


class ResponseProjectEvents(ResponsePaginatedQuery):
    events: list[ProjectEvent]


class ResponseClosings(ResponsePaginatedQuery):
    events: list[IssueClosed]


class ResponseIssue(ResponseRecord):
    issue: Issue


class QueryIssueEvents(DomainEvents[Issue, ResponseProjectEvents]):
    pass


class QueryIssueClosings(DomainEvents[Issue, ResponseClosings]):
    pass


class QueryGetIssue(Get[Issue, ResponseIssue]):
    pass


class QuerySandboxEvents(DomainEvents[Sandbox, ResponseProjectEvents]):
    pass


class QueryWorkspaceEvents(DomainEvents[Workspace, ResponseProjectEvents]):
    pass


class QueryAccountEvents(DomainEvents[Account, ResponseProjectEvents]):
    pass


class ResponseIssues(ResponsePaginatedQuery):
    issues: list[Issue]


class QueryNotEvents(DomainEvents[Issue, ResponseIssues]):
    pass


def project(*records: Entity) -> tuple[UseFramework, MemoryRepository]:
    bus = UseFramework("project-history", log_after_execution=False)
    repository = MemoryRepository()
    bus.add_dependency("repository", repository)
    for one in records:
        repository.save(one)

    @bus.feature([QueryGetIssue, QueryIssueEvents, QueryIssueClosings, QueryNotEvents])
    class IssueReads(EntityReads[Issue]):
        pass

    @bus.feature(QuerySandboxEvents)
    class SandboxReads(EntityReads[Sandbox]):
        pass

    @bus.feature(QueryWorkspaceEvents)
    class WorkspaceReads(EntityReads[Workspace]):
        pass

    @bus.feature(QueryAccountEvents)
    class AccountReads(EntityReads[Account]):
        pass

    return bus, repository


def an_issue_opened_and_closed(repository: MemoryRepository, title: str) -> Issue:
    issue = Issue(title=title)
    issue.open()
    repository.save(issue)
    issue.close("fixed")
    repository.save(issue)
    return issue


def names(answer: ResponseProjectEvents) -> list[str]:
    return [type(one).__name__ for one in answer.events]


def test_a_history_is_the_record_s_events_oldest_first():
    bus, repository = project()
    issue = an_issue_opened_and_closed(repository, "login")
    an_issue_opened_and_closed(repository, "other")

    answer = bus(QueryIssueEvents(id=issue.id), ResponseProjectEvents)

    assert names(answer) == ["IssueOpened", "IssueClosed"]
    assert {one.entity_id for one in answer.events} == {issue.id}


def test_a_caller_time_filter_adds_to_the_record_and_its_order_replaces_the_default():
    bus, repository = project()
    issue = an_issue_opened_and_closed(repository, "login")
    later = bus(QueryIssueEvents(id=issue.id), ResponseProjectEvents).events[1].created_at

    since = bus(
        QueryIssueEvents(
            id=issue.id,
            criteria=Criteria(
                where=Condition(field="created_at", operator=Operator.GTE, value=later)
            ),
        ),
        ResponseProjectEvents,
    )
    newest_first = bus(
        QueryIssueEvents(id=issue.id, criteria=Criteria(order=parse_order("-id"))),
        ResponseProjectEvents,
    )
    future = bus(
        QueryIssueEvents(
            id=issue.id,
            criteria=Criteria(
                where=Condition(
                    field="created_at",
                    operator=Operator.GTE,
                    value=datetime(2999, 1, 1, tzinfo=UTC),
                )
            ),
        ),
        ResponseProjectEvents,
    )

    assert names(since) == ["IssueClosed"]
    assert names(newest_first) == ["IssueClosed", "IssueOpened"]
    assert future.events == []


def test_two_kinds_of_record_with_the_same_id_never_mix():
    sandbox = Sandbox(id="dev-1")
    workspace = Workspace(id="dev-1")
    sandbox.reserve()
    workspace.create()
    bus, _repository = project(sandbox, workspace)

    of_sandbox = bus(QuerySandboxEvents(id="dev-1"), ResponseProjectEvents)
    of_workspace = bus(QueryWorkspaceEvents(id="dev-1"), ResponseProjectEvents)

    assert names(of_sandbox) == ["SandboxReserved"]
    assert names(of_workspace) == ["WorkspaceCreated"]


def test_a_key_other_than_the_identity_finds_the_record_first():
    account = Account(code="1.2.3")
    account.open()
    bus, _repository = project(account)

    answer = bus(QueryAccountEvents(id="1.2.3"), ResponseProjectEvents)

    assert names(answer) == ["IssueOpened"]
    assert answer.events[0].entity_id == account.id
    with pytest.raises(AggregateNotFound, match="Account 9.9.9"):
        bus(QueryAccountEvents(id="9.9.9"), ResponseProjectEvents)


def test_a_blank_key_is_refused_rather_than_reading_the_events_about_no_record():
    bus, _repository = project()

    with pytest.raises(ProgrammingError, match="key"):
        bus(QueryIssueEvents(id="  "), ResponseProjectEvents)


def test_a_response_that_does_not_hold_events_is_refused_naming_it():
    bus, repository = project()
    issue = an_issue_opened_and_closed(repository, "login")

    with pytest.raises(ProgrammingError, match="ResponseIssues"):
        bus(QueryNotEvents(id=issue.id), ResponseIssues)


def test_a_narrower_event_class_reads_only_those_events():
    bus, repository = project()
    issue = an_issue_opened_and_closed(repository, "login")

    answer = bus(QueryIssueClosings(id=issue.id), ResponseClosings)

    assert [one.resolution for one in answer.events] == ["fixed"]


def test_history_of_reads_several_records_together_each_by_its_type():
    sandbox = Sandbox(id="dev-1")
    workspace = Workspace(id="dev-1")
    sandbox.reserve()
    workspace.create()
    issue = Issue(title="login")
    issue.open()
    run = Run(issue_id=issue.id)
    run.start()
    _bus, repository = project(sandbox, workspace, issue, run)

    together = repository.search(
        ProjectEvent, history_of(issue, run).replaced_by(Criteria(order=parse_order("id")))
    )
    alone = repository.search(ProjectEvent, history_of(sandbox))

    assert [type(one).__name__ for one in together.items] == ["IssueOpened", "RunStarted"]
    assert [type(one).__name__ for one in alone.items] == ["SandboxReserved"]
    with pytest.raises(ProgrammingError, match="at least one"):
        history_of()


def test_the_history_sits_beside_the_record_s_other_reads_on_one_feature():
    bus, repository = project()
    issue = an_issue_opened_and_closed(repository, "login")

    record = bus(QueryGetIssue(id=issue.id), ResponseIssue)
    history = bus(QueryIssueEvents(id=issue.id), ResponseProjectEvents)

    assert record.issue.title == "login"
    assert names(history) == ["IssueOpened", "IssueClosed"]


def test_the_wire_names_keep_only_those_events_of_the_record():
    bus, repository = project()
    issue = an_issue_opened_and_closed(repository, "login")
    an_issue_opened_and_closed(repository, "other")

    closed = bus(QueryIssueEvents(id=issue.id, names=["IssueClosed"]), ResponseProjectEvents)
    one_name = bus(QueryIssueEvents(id=issue.id, names="IssueOpened"), ResponseProjectEvents)
    unknown = bus(
        QueryIssueEvents(id=issue.id, names=["nobody.records.this"]), ResponseProjectEvents
    )

    assert names(closed) == ["IssueClosed"]
    assert closed.events[0].entity_id == issue.id
    assert names(one_name) == ["IssueOpened"]
    assert unknown.events == []


def test_the_wire_names_combine_with_a_caller_time_filter():
    bus, repository = project()
    issue = an_issue_opened_and_closed(repository, "login")
    future = Criteria(
        where=Condition(
            field="created_at", operator=Operator.GTE, value=datetime(2999, 1, 1, tzinfo=UTC)
        )
    )

    later = bus(
        QueryIssueEvents(id=issue.id, names=["IssueOpened", "IssueClosed"], criteria=future),
        ResponseProjectEvents,
    )
    both = bus(
        QueryIssueEvents(id=issue.id, names=["IssueOpened", "IssueClosed"]),
        ResponseProjectEvents,
    )

    assert later.events == []
    assert names(both) == ["IssueOpened", "IssueClosed"]


def test_the_context_log_filters_by_wire_name_like_any_field():
    _bus, repository = project()
    an_issue_opened_and_closed(repository, "login")
    an_issue_opened_and_closed(repository, "other")

    closings = repository.search(
        ProjectEvent,
        Criteria(where=Condition(field="name", value=["IssueClosed"], operator=Operator.IN)),
    )

    assert [type(one).__name__ for one in closings.items] == ["IssueClosed", "IssueClosed"]
    assert closings.dropped == ()
