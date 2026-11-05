"""The entities a use case concerns: a category every reader of the bus groups it by — and what
it is for them, read off its DTO's type.

A support desk: issues live in projects. Moving an issue is reached from both screens, so it
concerns both entities; reading an issue concerns it because its DTO says so; closing the month
concerns no entity and belongs to the context alone. Nothing here changes how the bus runs.
Reading an issue is its `get`; moving it, a `command` — and every wire says so.
"""

from dataclasses import dataclass

import pytest

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    UseFramework,
    exceptions,
)
from sincpro_framework.ddd import (
    Edit,
    EntityReads,
    Get,
    ResponseEdit,
    ResponsePaginatedQuery,
    ResponseRecord,
    Search,
)
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.entrypoints import (
    FastApiGateway,
    GrpcGateway,
    Profile,
    RestGateway,
    RpcGateway,
)
from sincpro_framework.entrypoints.entrypoint.decorators import grpc, rpc
from sincpro_framework.introspection import entity_tags, operations_of
from sincpro_framework.introspection.entity_tags import Role, entities_of, role_of


@dataclass
class Project(Entity):
    name: str = ""


@dataclass
class Issue(Entity):
    title: str = ""
    project_id: str = ""


class CommandMoveIssue(DataTransferObject):
    issue_id: str
    project_id: str


class CommandCloseMonth(DataTransferObject):
    month: str


class ResponseIssue(ResponseRecord):
    issue: Issue


class QueryGetIssue(Get[Issue, ResponseIssue]):
    pass


def a_desk() -> UseFramework:
    bus = UseFramework("desk", log_after_execution=False)
    bus.add_dependency("repository", None)

    @bus.feature(CommandMoveIssue)
    @entity_tags(Issue, Project)
    @rpc()
    @grpc()
    class MoveIssue(Feature):
        def execute(self, dto: CommandMoveIssue) -> None:
            return None

    @bus.feature(QueryGetIssue)
    @rpc()
    @grpc()
    class IssueReads(EntityReads[Issue]):
        pass

    @bus.app_service(CommandCloseMonth)
    @rpc()
    class CloseMonth(ApplicationService):
        def execute(self, dto: CommandCloseMonth) -> None:
            return None

    bus.build_root_bus()
    return bus


# ---------------------------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------------------------


def test_the_decorator_returns_the_class_as_it_is():
    class MergeIssues(Feature):
        pass

    assert entity_tags(Issue)(MergeIssues) is MergeIssues
    assert not hasattr(MergeIssues, "entities")


def test_what_the_handler_and_its_command_declare_is_read_together_each_once():
    @entity_tags(Issue)
    class CommandLinkIssues(DataTransferObject):
        pass

    @entity_tags(Project, Issue)
    class LinkIssues(Feature):
        pass

    assert entities_of(LinkIssues, CommandLinkIssues) == (Project, Issue)


def test_a_framework_dto_concerns_the_entity_its_type_names():
    assert entities_of(None, QueryGetIssue) == (Issue,)


def test_a_use_case_that_declares_nothing_belongs_to_its_context_alone():
    assert entities_of(None, CommandCloseMonth) == ()


class ResponseIssues(ResponsePaginatedQuery):
    issues: list[Issue]


class ProjectSearch[T, R: ResponsePaginatedQuery](Search[T, R]):
    """A project's own base over `Search`: every list of it narrowed by the project."""

    project_id: str = ""


class QueryListProjectIssues(ProjectSearch[Issue, ResponseIssues]):
    pass


class ResponseEditIssue(ResponseEdit):
    issue: Issue


class CommandEditIssue(Edit[Issue, ResponseEditIssue]):
    pass


def test_what_a_use_case_is_for_its_entity_is_the_framework_dto_it_descends_from():
    assert role_of(QueryGetIssue) is Role.GET
    assert role_of(CommandEditIssue) is Role.EDIT
    assert role_of(QueryListProjectIssues) is Role.SEARCH  # through the project's own base
    assert role_of(CommandMoveIssue) is Role.COMMAND  # tagged: still a command
    assert role_of(CommandCloseMonth) is Role.COMMAND


def test_tagging_with_no_entity_or_with_a_name_is_refused_showing_the_form():
    with pytest.raises(exceptions.ProgrammingError, match="it was given none"):
        entity_tags()
    with pytest.raises(exceptions.ProgrammingError, match="@entity_tags\\(Issue\\)"):
        entity_tags("Issue")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------
# What reads the bus
# ---------------------------------------------------------------------------------------------


def test_the_operations_of_a_bus_say_the_entities_each_one_concerns():
    operations = operations_of(a_desk())

    assert operations["CommandMoveIssue"].entities == (Issue, Project)
    assert operations["QueryGetIssue"].entities == (Issue,)
    assert operations["CommandCloseMonth"].entities == ()
    assert operations["CommandMoveIssue"].to_json()["entities"] == ["Issue", "Project"]


def test_the_operations_of_a_bus_say_what_each_one_is_for_its_entity():
    operations = operations_of(a_desk())

    assert operations["QueryGetIssue"].role is Role.GET
    assert operations["CommandMoveIssue"].role is Role.COMMAND
    assert operations["QueryGetIssue"].to_json()["role"] == "get"


def test_the_bus_runs_a_tagged_use_case_as_any_other():
    bus = a_desk()

    assert bus(CommandMoveIssue(issue_id="i1", project_id="p2")) is None


def test_openrpc_tags_each_method_with_the_entities_it_concerns():
    document = RpcGateway({"desk": a_desk()}, unguarded=True).discover()
    tags = {
        one["name"]: [tag["name"] for tag in one.get("tags", [])]
        for one in document["methods"]
    }

    assert tags["desk.move_issue"] == ["desk", "Issue", "Project"]
    assert tags["desk.close_month"] == ["desk"]


def test_openapi_tags_each_operation_with_the_entities_it_concerns():
    document = FastApiGateway([a_desk()], profile=Profile.RPC, unguarded=True).app().openapi()
    tags = {
        operation["operationId"]: operation["tags"]
        for item in document["paths"].values()
        for operation in item.values()
    }

    assert tags["CommandMoveIssue"] == ["desk", "Issue", "Project"]
    assert tags["QueryGetIssue"] == ["desk", "Issue"]
    assert tags["CommandCloseMonth"] == ["desk"]


def test_the_starlette_rest_document_tags_them_too():
    document = RestGateway({"desk": a_desk()}).openapi()
    tags = [
        operation["tags"]
        for item in document["paths"].values()
        for operation in item.values()
    ]

    assert ["desk", "features", "Issue", "Project"] in tags


def test_every_wire_says_what_each_operation_is_for_its_entity():
    rpc_methods = RpcGateway({"desk": a_desk()}, unguarded=True).discover()["methods"]
    fastapi = FastApiGateway([a_desk()], profile=Profile.RPC, unguarded=True).app().openapi()
    starlette = RestGateway({"desk": a_desk()}).openapi()
    described = GrpcGateway({"desk": a_desk()}, unguarded=True).describe()

    assert {
        one["x-sincpro-dto"]: one["x-sincpro-role"]
        for one in rpc_methods
        if "x-sincpro-dto" in one  # rpc.discover is the document's own
    } == {
        "CommandMoveIssue": "command",
        "QueryGetIssue": "get",
        "CommandCloseMonth": "command",
    }
    for document in (fastapi, starlette):
        roles = {
            operation["x-sincpro-dto"]: operation["x-sincpro-role"]
            for item in document["paths"].values()
            for operation in item.values()
        }
        assert roles["QueryGetIssue"] == "get"
        assert roles["CommandMoveIssue"] == "command"
    assert {
        method["command"]: method["role"]
        for service in described["services"]
        for method in service["methods"]
    } == {"CommandMoveIssue": "command", "QueryGetIssue": "get"}
