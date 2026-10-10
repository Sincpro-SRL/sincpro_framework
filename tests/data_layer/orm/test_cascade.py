"""A root is saved and removed whole: the children it owns go with it, and only an assignment
says which of them are gone."""

import pytest
from sqlalchemy import select

from sincpro_framework import ProgrammingError
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.services import cascade
from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.exceptions import ConstraintViolation, StaleAggregate

from .cascade_models import (
    Check,
    CodeRepo,
    Doc,
    Folder,
    Issue,
    Label,
    Member,
    Note,
    Tag,
    Task,
    Workspace,
    code_repo_table,
    declare,
    forge,
    note_table,
)
from .engines import fresh


@pytest.fixture
def repository(engine_url: str) -> Repository:
    declare()
    return Repository(fresh(engine_url, forge.metadata, enforce_foreign_keys=True))


class Heard:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str) -> None:
        self.warnings.append(message)


@pytest.fixture
def warnings(monkeypatch) -> list[str]:
    heard = Heard()
    monkeypatch.setattr(cascade, "logger", heard)
    return heard.warnings


def names(repository: Repository, model: type) -> list[str]:
    field = "text" if model is Note else "name"
    return sorted(repository.pluck(model, field))


def stored_workspace(repository: Repository, *repos: str) -> Workspace:
    workspace = Workspace(code="sp-1", repositories=[CodeRepo(name=n) for n in repos])
    repository.save(workspace)
    return workspace


# ── saving the root writes its children ──────────────────────────────────────────────────


def test_a_new_root_writes_its_children_with_its_key_on_them(repository):
    workspace = stored_workspace(repository, "api", "web")

    [api, web] = repository.fetch_all(CodeRepo).items
    assert {api.workspace_id, web.workspace_id} == {workspace.id}
    assert names(repository, CodeRepo) == ["api", "web"]


def test_the_children_of_children_are_written_too(repository):
    repository.save(
        Workspace(
            code="sp-1", repositories=[CodeRepo(name="api", checks=[Check(name="lint")])]
        )
    )

    [check] = repository.fetch_all(Check).items
    assert check.repo_id == repository.get_by(CodeRepo, name="api").id


def test_assigning_the_children_removes_the_ones_the_root_no_longer_holds(repository):
    stored_workspace(repository, "api", "web")

    with repository.context() as unit:
        workspace = unit.get_by(Workspace, code="sp-1")
        workspace.repositories = [CodeRepo(name="cli")]
        unit.save(workspace)

    assert names(repository, CodeRepo) == ["cli"]


def test_rebuilding_from_outside_a_unit_of_work_keeps_the_ones_still_held(repository):
    workspace = stored_workspace(repository, "api", "web")
    [api] = [one for one in repository.fetch_all(CodeRepo).items if one.name == "api"]

    workspace.repositories = [api, CodeRepo(name="cli")]
    repository.save(workspace)

    assert names(repository, CodeRepo) == ["api", "cli"]
    assert repository.get_by(CodeRepo, name="api").id == api.id


def test_a_removed_child_takes_its_own_children_with_it(repository):
    repository.save(
        Workspace(
            code="sp-1", repositories=[CodeRepo(name="api", checks=[Check(name="lint")])]
        )
    )

    with repository.context() as unit:
        workspace = unit.get_by(Workspace, code="sp-1")
        workspace.repositories = []
        unit.save(workspace)

    assert names(repository, CodeRepo) == [] and names(repository, Check) == []


def test_a_child_with_a_nullable_key_is_detached_not_deleted(repository):
    repository.save(Workspace(code="sp-1", notes=[Note(text="keep me")]))

    with repository.context() as unit:
        workspace = unit.get_by(Workspace, code="sp-1")
        workspace.notes = []
        unit.save(workspace)

    with repository.database.engine.connect() as connection:
        rows = connection.execute(select(note_table.c.text, note_table.c.workspace_id)).all()
    assert rows == [("keep me", None)]


# ── only an assignment removes ───────────────────────────────────────────────────────────


def test_a_relation_nobody_read_keeps_its_children(repository):
    stored_workspace(repository, "api", "web")

    workspace = repository.get_by(Workspace, code="sp-1")
    workspace.code = "sp-2"
    repository.save(workspace)

    assert names(repository, CodeRepo) == ["api", "web"]


def test_an_assignment_settles_only_what_its_reading_saw(repository):
    workspace = stored_workspace(repository, "api", "web")

    with repository.context() as unit:
        held = unit.get_by(Workspace, code="sp-1")
        [api, _web] = sorted(held.repositories, key=lambda one: one.name)
        repository.save(CodeRepo(workspace_id=workspace.id, name="added meanwhile"))
        held.repositories = [api]
        unit.save(held)

    assert names(repository, CodeRepo) == ["added meanwhile", "api"]


def test_assigning_a_relation_nobody_read_reads_it_first_inside_a_unit_of_work(repository):
    stored_workspace(repository, "api", "web")

    with repository.context() as unit:
        held = unit.get_by(Workspace, code="sp-1")
        held.repositories = [CodeRepo(name="cli")]
        unit.save(held)

    assert names(repository, CodeRepo) == ["cli"]


def test_a_batch_never_takes_a_child_moved_meanwhile_to_another_root_of_it(repository):
    first = stored_workspace(repository, "c")
    second = Workspace(code="sp-2")
    repository.save(second)

    with repository.context() as unit:
        old_root = unit.get(Workspace, first.id)
        new_root = unit.get(Workspace, second.id)
        assert [one.name for one in old_root.repositories] == ["c"]
        assert list(new_root.repositories) == []
        moved = repository.get_by(CodeRepo, name="c")
        moved.workspace_id = second.id
        repository.save(moved)
        old_root.repositories = []
        new_root.repositories = [CodeRepo(name="new")]
        unit.save([old_root, new_root])

    assert names(repository, CodeRepo) == ["c", "new"]


def test_a_cut_reading_inside_a_unit_of_work_is_read_whole_before_an_assignment(repository):
    stored_workspace(repository, "api", "web")
    only_api = Criteria.model_validate(
        {
            "where": {"field": "code", "value": "sp-1"},
            "specification": {"repositories": {"where": {"field": "name", "value": "api"}}},
        }
    )

    with repository.context() as unit:
        workspace = unit.first(Workspace, only_api)
        workspace.repositories = [CodeRepo(name="only")]
        unit.save(workspace)

    assert names(repository, CodeRepo) == ["only"]


def test_a_blind_assignment_outside_a_unit_of_work_saves_and_removes_nothing(
    repository, warnings
):
    stored_workspace(repository, "api")
    workspace = repository.get_by(Workspace, code="sp-1")

    workspace.repositories = [CodeRepo(name="cli")]
    repository.save(workspace)

    assert names(repository, CodeRepo) == ["api", "cli"]
    assert any("no whole reading" in said for said in warnings)


def test_moving_a_child_between_roots_in_two_saves_is_refused_with_its_remedy(repository):
    first = stored_workspace(repository, "api")
    second = Workspace(code="sp-2")
    repository.save(second)

    with repository.context() as unit:
        old_root = unit.get(Workspace, first.id)
        new_root = unit.get(Workspace, second.id)
        [api] = old_root.repositories
        old_root.repositories = []
        unit.save(old_root)
        new_root.repositories = [api]
        with pytest.raises(ProgrammingError, match="in one call"):
            unit.save(new_root)


def test_moving_a_child_between_roots_in_one_save_keeps_it(repository):
    first = stored_workspace(repository, "api")
    second = Workspace(code="sp-2")
    repository.save(second)

    with repository.context() as unit:
        old_root = unit.get(Workspace, first.id)
        new_root = unit.get(Workspace, second.id)
        [api] = old_root.repositories
        old_root.repositories = []
        new_root.repositories = [api]
        unit.save([old_root, new_root])

    assert repository.get_by(CodeRepo, name="api").workspace_id == second.id


def test_a_relation_that_was_read_removes_nothing_another_writer_added(repository):
    workspace = stored_workspace(repository, "api")

    with repository.context() as unit:
        held = unit.get_by(Workspace, code="sp-1")
        assert [one.name for one in held.repositories] == ["api"]
        repository.save(CodeRepo(workspace_id=workspace.id, name="added meanwhile"))
        held.code = "sp-2"
        unit.save(held)

    assert names(repository, CodeRepo) == ["added meanwhile", "api"]


def test_the_list_a_constructor_gave_removes_nothing_saved_later(repository):
    workspace = Workspace(code="sp-1")
    repository.save(workspace)
    repository.save(CodeRepo(workspace_id=workspace.id, name="api"))

    workspace.code = "sp-2"
    repository.save(workspace)

    assert names(repository, CodeRepo) == ["api"]


def test_an_assignment_is_carried_out_once(repository):
    workspace = stored_workspace(repository, "api")
    workspace.repositories = [CodeRepo(name="web")]
    repository.save(workspace)
    repository.save(CodeRepo(workspace_id=workspace.id, name="cli"))

    workspace.code = "sp-2"
    repository.save(workspace)

    assert names(repository, CodeRepo) == ["cli", "web"]


def test_a_cut_reading_assigned_over_saves_and_removes_nothing(repository, warnings):
    stored_workspace(repository, "api", "web", "cli")
    page = Criteria.model_validate(
        {"specification": {"repositories": {"where": {"field": "name", "value": "api"}}}}
    )
    workspace = repository.first(Workspace, page)

    workspace.repositories = [*workspace.repositories, CodeRepo(name="docs")]
    repository.save(workspace)

    assert names(repository, CodeRepo) == ["api", "cli", "docs", "web"]
    assert any("Workspace.repositories" in said for said in warnings)


def test_an_archived_child_is_not_an_orphan(repository):
    workspace = Workspace(code="sp-1", docs=[Doc(title="old"), Doc(title="live")])
    repository.save(workspace)
    repository.archive(repository.get_by(Doc, title="old"))

    with repository.context() as unit:
        held = unit.get_by(Workspace, code="sp-1")
        held.docs = list(held.docs)
        unit.save(held)

    archived = Criteria.model_validate(
        {"where": {"field": "archived_at", "operator": "is null", "value": False}}
    )
    assert [doc.title for doc in repository.fetch_all(Doc, archived).items] == ["old"]


# ── what is written, and what is not ─────────────────────────────────────────────────────


def test_an_unchanged_child_is_not_written_again(repository):
    stored_workspace(repository, "api")

    with repository.context() as unit:
        workspace = unit.get_by(Workspace, code="sp-1")
        workspace.repositories = list(workspace.repositories)
        workspace.code = "sp-2"
        unit.save(workspace)

    assert repository.get_by(CodeRepo, name="api").version == 1


def test_a_changed_child_is_written_with_its_version_checked(repository):
    stored_workspace(repository, "api")
    stale = repository.get_by(CodeRepo, name="api")

    with repository.context() as unit:
        workspace = unit.get_by(Workspace, code="sp-1")
        [api] = workspace.repositories
        api.name = "api-v2"
        workspace.repositories = [api]
        unit.save(workspace)

    assert repository.get_by(CodeRepo, name="api-v2").version == 2
    stale.name = "lost"
    with pytest.raises(StaleAggregate):
        repository.save(stale)


# ── the root's version moves with its parts ─────────────────────────────────────────────


def version_of(repository: Repository) -> int:
    workspace = repository.get_by(Workspace, code="sp-1")
    assert workspace is not None
    return workspace.version


def test_changing_a_part_raises_the_version_of_its_root(repository):
    stored_workspace(repository, "api")

    with repository.context() as unit:
        workspace = unit.get_by(Workspace, code="sp-1")
        [api] = workspace.repositories
        api.name = "api-v2"
        unit.save(workspace)

    assert version_of(repository) == 2


def test_adding_a_part_raises_the_version_of_its_root(repository):
    stored_workspace(repository, "api")

    with repository.context() as unit:
        workspace = unit.get_by(Workspace, code="sp-1")
        workspace.repositories = [*workspace.repositories, CodeRepo(name="web")]
        unit.save(workspace)

    assert version_of(repository) == 2


def test_dropping_a_part_raises_the_version_of_its_root(repository):
    stored_workspace(repository, "api", "web")

    with repository.context() as unit:
        workspace = unit.get_by(Workspace, code="sp-1")
        workspace.repositories = [one for one in workspace.repositories if one.name == "api"]
        unit.save(workspace)

    assert version_of(repository) == 2
    assert names(repository, CodeRepo) == ["api"]


def test_a_grandchild_that_changes_moves_the_root_it_belongs_to(repository):
    repository.save(
        Workspace(
            code="sp-1", repositories=[CodeRepo(name="api", checks=[Check(name="lint")])]
        )
    )

    with repository.context() as unit:
        workspace = unit.get_by(Workspace, code="sp-1")
        [api] = workspace.repositories
        [lint] = api.checks
        lint.name = "lint-strict"
        unit.save(workspace)

    assert version_of(repository) == 2
    assert repository.get_by(CodeRepo, name="api").version == 1


def test_saving_a_root_whose_parts_did_not_move_leaves_its_version(repository):
    stored_workspace(repository, "api")

    with repository.context() as unit:
        workspace = unit.get_by(Workspace, code="sp-1")
        assert [one.name for one in workspace.repositories] == ["api"]
        unit.save(workspace)

    assert version_of(repository) == 1


def test_two_writers_editing_different_parts_of_one_root_do_not_both_win(repository):
    stored_workspace(repository, "api", "web")
    with repository.context() as unit:
        first = unit.get_by(Workspace, code="sp-1")
        assert len(first.repositories) == 2

    with repository.context() as unit:
        second = unit.get_by(Workspace, code="sp-1")
        [web] = [one for one in second.repositories if one.name == "web"]
        web.name = "web-v2"
        unit.save(second)

    [api] = [one for one in first.repositories if one.name == "api"]
    api.name = "api-v2"
    with pytest.raises(StaleAggregate):
        repository.save(first)
    assert names(repository, CodeRepo) == ["api", "web-v2"]


def test_a_reference_is_never_written_by_its_root(repository):
    workspace = Workspace(code="sp-1", members=[Member(name="ana")])
    repository.save(workspace)

    assert repository.fetch_all(Member).items == ()


# ── removing the root ────────────────────────────────────────────────────────────────────


def test_removing_the_root_removes_what_it_owns_and_detaches_the_rest(repository):
    repository.save(
        Workspace(
            code="sp-1",
            repositories=[CodeRepo(name="api", checks=[Check(name="lint")])],
            notes=[Note(text="keep me")],
        )
    )

    repository.remove(repository.get_by(Workspace, code="sp-1"))

    assert repository.fetch_all(Workspace).items == ()
    assert names(repository, CodeRepo) == [] and names(repository, Check) == []
    with repository.database.engine.connect() as connection:
        assert connection.execute(select(note_table.c.workspace_id)).scalars().all() == [None]


def test_removing_the_root_takes_every_child_its_scope_does_not_show_too(repository):
    workspace = Workspace(code="sp-1")
    repository.save(workspace)
    repository.save(
        [
            Issue(workspace_id=workspace.id, state="open"),
            Issue(workspace_id=workspace.id, state="closed"),
        ]
    )

    repository.remove(repository.get_by(Workspace, code="sp-1"))

    assert repository.fetch_all(Issue).items == ()


def test_a_part_goes_with_its_root_and_a_reference_is_left_to_its_foreign_key(repository):
    workspace = Workspace(code="sp-1", docs=[Doc(title="contract")])
    repository.save(workspace)

    repository.remove(repository.get_by(Workspace, code="sp-1"))
    assert repository.fetch_all(Doc).items == ()

    referenced = Workspace(code="sp-2")
    repository.save(referenced)
    repository.save(Member(workspace_id=referenced.id, name="ana"))
    with pytest.raises(ConstraintViolation, match="foreign key"):
        repository.remove(repository.get_by(Workspace, code="sp-2"))
    assert [member.name for member in repository.fetch_all(Member).items] == ["ana"]


def test_removing_the_root_reads_its_children_even_when_nobody_did(repository):
    stored_workspace(repository, "api", "web")

    with repository.context() as unit:
        unit.remove(unit.get_by(Workspace, code="sp-1"))

    with repository.database.engine.connect() as connection:
        assert connection.execute(select(code_repo_table.c.id)).all() == []


def test_a_relation_joined_on_a_field_other_roots_share_is_never_written_or_removed_by_one(
    repository,
):
    vat, also_vat = Tag(label="VAT"), Tag(label="VAT")
    repository.save([vat, also_vat])
    repository.save([Label(label="VAT", text=f"line {n}") for n in range(3)])

    repository.remove(repository.get(Tag, vat.id))

    assert len(repository.fetch_all(Label).items) == 3


def test_a_folder_that_is_its_own_parent_is_removed_once_from_outside_a_unit_of_work(
    repository,
):
    root = Folder(name="root")
    root.parent_id = root.id
    repository.save(root)

    repository.remove(repository.get(Folder, root.id))

    assert repository.fetch_all(Folder).items == ()


# ── by default, nothing is removed or detached that nobody declared ─────────────────────


def test_removing_a_root_whose_relation_says_nothing_is_refused_before_anything_runs(
    repository,
):
    workspace = Workspace(code="sp-1", tasks=[Task(title="ship it")])
    repository.save(workspace)

    with pytest.raises(ProgrammingError, match="Workspace.tasks.*Orphans.DELETE"):
        repository.remove(repository.get_by(Workspace, code="sp-1"))

    assert repository.get_by(Workspace, code="sp-1") is not None
    assert [task.title for task in repository.fetch_all(Task).items] == ["ship it"]


def test_dropping_a_child_of_a_relation_that_says_nothing_is_refused(repository):
    workspace = Workspace(code="sp-1", tasks=[Task(title="a"), Task(title="b")])
    repository.save(workspace)

    with pytest.raises(ProgrammingError, match="the assignment drops 1 Task"):
        with repository.context() as unit:
            held = unit.get_by(Workspace, code="sp-1")
            held.tasks = [one for one in held.tasks if one.title == "a"]
            unit.save(held)

    assert sorted(repository.pluck(Task, "title")) == ["a", "b"]


def test_a_root_with_nothing_under_a_silent_relation_is_removed(repository):
    repository.save(Workspace(code="sp-1"))

    repository.remove(repository.get_by(Workspace, code="sp-1"))

    assert repository.fetch_all(Workspace).items == ()
