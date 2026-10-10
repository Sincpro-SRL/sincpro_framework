"""A record's history on a database: the context's one event table, read by `DomainEvents` on the
record's own `EntityReads`, and by `history_of` for several records at once.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import Column, Text
from sqlalchemy.orm import registry

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.orm import (
    Repository,
    map_aggregates,
    map_events,
    template_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.ddd import (
    Condition,
    Criteria,
    DomainEvent,
    DomainEvents,
    Entity,
    EntityReads,
    ResponsePaginatedQuery,
    history_of,
)
from sincpro_framework.ddd.criteria import Operator, parse_order

from .engines import fresh


@dataclass(kw_only=True)
class SiteEvent(DomainEvent):
    name = "tests.history.v1.event"


@dataclass(kw_only=True)
class SiteReserved(SiteEvent):
    name = "tests.history.sandbox.v1.reserved"
    by: str = ""


@dataclass(kw_only=True)
class SiteReleased(SiteEvent):
    name = "tests.history.sandbox.v1.released"


@dataclass(kw_only=True)
class FolderCreated(SiteEvent):
    name = "tests.history.workspace.v1.created"


@dataclass
class Site(Entity):
    label: str = ""

    def reserve(self, by: str) -> None:
        self.record(SiteReserved(by=by))

    def release(self) -> None:
        self.record(SiteReleased())


@dataclass
class Folder(Entity):
    label: str = ""

    def create(self) -> None:
        self.record(FolderCreated())


histories = registry()
site_table = entity_table(
    "hs_site", histories.metadata, Column("label", Text, nullable=False)
)
folder_table = entity_table(
    "hs_folder", histories.metadata, Column("label", Text, nullable=False)
)
site_events = template_table.event_table("hs_site_events", histories.metadata)
map_aggregates(histories, {Site: site_table, Folder: folder_table})
map_events(histories, SiteEvent, site_events)


class ResponseSiteEvents(ResponsePaginatedQuery):
    events: list[SiteEvent]


class QuerySiteEvents(DomainEvents[Site, ResponseSiteEvents]):
    pass


class QueryFolderEvents(DomainEvents[Folder, ResponseSiteEvents]):
    pass


@pytest.fixture
def repository(engine_url: str) -> Repository:
    return Repository(fresh(engine_url, histories.metadata))


def site_reads(repository: Repository) -> UseFramework:
    bus = UseFramework("site-history", log_after_execution=False)
    bus.add_dependency("repository", repository)

    @bus.feature(QuerySiteEvents)
    class SiteReads(EntityReads[Site]):
        pass

    @bus.feature(QueryFolderEvents)
    class FolderReads(EntityReads[Folder]):
        pass

    return bus


def a_site_and_a_folder_sharing_an_id(repository: Repository) -> None:
    with repository.context() as unit:
        site = Site(id="dev-1", label="sandbox")
        site.reserve("ana")
        unit.save(site)
        folder = Folder(id="dev-1", label="workspace")
        folder.create()
        unit.save(folder)
    with repository.context() as unit:
        again = unit.get(Site, "dev-1")
        assert again is not None
        again.release()
        unit.save(again)


def names(answer: ResponseSiteEvents) -> list[str]:
    return [type(one).__name__ for one in answer.events]


def test_a_record_s_history_is_its_events_oldest_first_and_never_another_kind_s(
    repository,
):
    a_site_and_a_folder_sharing_an_id(repository)
    bus = site_reads(repository)

    of_site = bus(QuerySiteEvents(id="dev-1"), ResponseSiteEvents)
    of_folder = bus(QueryFolderEvents(id="dev-1"), ResponseSiteEvents)

    reserved = of_site.events[0]
    assert names(of_site) == ["SiteReserved", "SiteReleased"]
    assert isinstance(reserved, SiteReserved) and reserved.by == "ana"
    assert names(of_folder) == ["FolderCreated"]


def test_a_caller_time_filter_adds_and_its_order_replaces(repository):
    a_site_and_a_folder_sharing_an_id(repository)
    bus = site_reads(repository)

    newest_first = bus(
        QuerySiteEvents(id="dev-1", criteria=Criteria(order=parse_order("-id"))),
        ResponseSiteEvents,
    )
    none_yet = bus(
        QuerySiteEvents(
            id="dev-1",
            criteria=Criteria(
                where=Condition(
                    field="created_at",
                    operator=Operator.GTE,
                    value=datetime(2999, 1, 1, tzinfo=UTC),
                )
            ),
        ),
        ResponseSiteEvents,
    )

    assert names(newest_first) == ["SiteReleased", "SiteReserved"]
    assert none_yet.events == []


def test_history_of_reads_several_records_each_by_its_type(repository):
    a_site_and_a_folder_sharing_an_id(repository)
    site = Site(id="dev-1")
    folder = Folder(id="dev-1")

    both = repository.search(
        SiteEvent, history_of(site, folder).replaced_by(Criteria(order=parse_order("id")))
    )
    only_folder = repository.search(SiteEvent, history_of(folder))

    assert [type(one).__name__ for one in both.items] == [
        "SiteReserved",
        "FolderCreated",
        "SiteReleased",
    ]
    assert [type(one).__name__ for one in only_folder.items] == ["FolderCreated"]


def test_the_wire_names_keep_only_those_events_with_the_subject_and_a_time_filter(repository):
    a_site_and_a_folder_sharing_an_id(repository)
    bus = site_reads(repository)

    released = bus(
        QuerySiteEvents(id="dev-1", names="tests.history.sandbox.v1.released"),
        ResponseSiteEvents,
    )
    folder_name_on_site = bus(
        QuerySiteEvents(id="dev-1", names=["tests.history.workspace.v1.created"]),
        ResponseSiteEvents,
    )
    none_later = bus(
        QuerySiteEvents(
            id="dev-1",
            names=["tests.history.sandbox.v1.reserved"],
            criteria=Criteria(
                where=Condition(
                    field="created_at",
                    operator=Operator.GTE,
                    value=datetime(2999, 1, 1, tzinfo=UTC),
                )
            ),
        ),
        ResponseSiteEvents,
    )

    assert names(released) == ["SiteReleased"]
    assert folder_name_on_site.events == []
    assert none_later.events == []


def test_the_context_log_filters_by_wire_name_on_sql(repository):
    a_site_and_a_folder_sharing_an_id(repository)

    page = repository.search(
        SiteEvent,
        Criteria(
            where=Condition(
                field="name",
                value=[
                    "tests.history.sandbox.v1.reserved",
                    "tests.history.workspace.v1.created",
                ],
                operator=Operator.IN,
            ),
            order=parse_order("id"),
        ),
    )

    assert [type(one).__name__ for one in page.items] == ["SiteReserved", "FolderCreated"]
    assert page.dropped == ()
