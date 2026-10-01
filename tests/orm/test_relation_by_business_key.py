"""A foreign key to a unique column that is not the primary key resolves by that column, on
both sides — never by the identity, which would match nothing and look like «no children»."""

import pytest

from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.orm.sqlalchemy.domain.registry import relations_of
from sincpro_framework.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.orm.sqlalchemy.infrastructure.database import Database

from .business_key_models import CodeRepository, Workspace, declare, forge


@pytest.fixture(scope="module")
def forge_repository(tmp_path_factory) -> Repository:
    declare()
    database = Database(f"sqlite:///{tmp_path_factory.mktemp('business_key')}/forge.sqlite3")
    forge.metadata.create_all(database.engine)
    repository = Repository(database)
    with repository.context() as unit:
        unit.save([Workspace(code="sp-1"), Workspace(code="sp-2")])
        unit.save(
            [
                CodeRepository(workspace_code="sp-1", name="a"),
                CodeRepository(workspace_code="sp-1", name="b"),
                CodeRepository(workspace_code="sp-2", name="c"),
            ]
        )
    return repository


@pytest.mark.usefixtures("forge_repository")
def test_the_inferred_relation_names_both_sides_by_the_referenced_column():
    repositories = relations_of(Workspace)["repositories"]
    workspace = relations_of(CodeRepository)["workspace"]
    assert (repositories.parent_field, repositories.related_field) == (
        "code",
        "workspace_code",
    )
    assert (workspace.parent_field, workspace.related_field) == ("workspace_code", "code")


def test_a_one2many_by_a_business_key_brings_its_children(forge_repository):
    found = forge_repository.first(
        Workspace,
        Criteria.model_validate(
            {
                "where": {"field": "code", "value": "sp-1"},
                "specification": {"repositories": {}},
            }
        ),
    )
    assert sorted(r.name for r in found.repositories) == ["a", "b"]


def test_a_many2one_by_a_business_key_brings_its_parent(forge_repository):
    found = forge_repository.first(
        CodeRepository,
        Criteria.model_validate(
            {"where": {"field": "name", "value": "c"}, "specification": {"workspace": {}}}
        ),
    )
    assert found.workspace.code == "sp-2"


def test_inside_a_unit_of_work_the_business_key_resolves_whole(forge_repository):
    with forge_repository.context() as unit:
        workspace = unit.get_by(Workspace, code="sp-2")
        assert [r.name for r in workspace.repositories] == ["c"]
        child = unit.get_by(CodeRepository, name="a")
        assert child.workspace.code == "sp-1"
