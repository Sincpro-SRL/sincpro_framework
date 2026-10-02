"""An aggregate with every kind of child a cascade has to tell apart.

Workspace.repositories   Orphans.DELETE               an orphan is deleted, and so on remove
CodeRepo.checks          Orphans.DELETE               a grandchild, removed with its parent
Workspace.notes          Orphans.DETACH               an orphan's key is set to NULL, the row stays
Workspace.members        owned=False                  a reference: read, never written or removed
Workspace.docs           Orphans.DELETE, archivable   an archived child is not an orphan
Workspace.open_issues    Orphans.DELETE, scoped       removed with the root, closed ones included
Workspace.tasks          nothing declared             removing or dropping one is refused
Tag.labels               joined on a shared label     never the tag's: other tags reach the same rows
Folder.children          self-referencing             a folder that is its own parent removes once
"""

from dataclasses import dataclass, field

from sqlalchemy import Column, ForeignKey, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd.criteria import Condition, Criteria
from sincpro_framework.ddd.entity import ArchivableMixin, Entity
from sincpro_framework.orm.sqlalchemy.domain.relations import Orphans
from sincpro_framework.orm.sqlalchemy.entrypoint.templates import (
    archive_columns,
    entity_table,
)
from sincpro_framework.orm.sqlalchemy.services.data_mapper import Relation, map_aggregates


@dataclass
class Check(Entity):
    repo_id: str = ""
    name: str = ""


@dataclass
class CodeRepo(Entity):
    workspace_id: str = ""
    name: str = ""
    checks: list[Check] = field(default_factory=list)


@dataclass
class Note(Entity):
    workspace_id: str | None = None
    text: str = ""


@dataclass
class Member(Entity):
    workspace_id: str = ""
    name: str = ""


@dataclass
class Doc(ArchivableMixin, Entity):
    workspace_id: str = ""
    title: str = ""


@dataclass
class Issue(Entity):
    workspace_id: str = ""
    state: str = "open"


@dataclass
class Task(Entity):
    workspace_id: str | None = None
    title: str = ""


@dataclass
class Workspace(Entity):
    code: str = ""
    repositories: list[CodeRepo] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)
    docs: list[Doc] = field(default_factory=list)
    open_issues: list[Issue] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)


@dataclass
class Label(Entity):
    label: str = ""
    text: str = ""


@dataclass
class Tag(Entity):
    label: str = ""
    labels: list[Label] = field(default_factory=list)


@dataclass
class Folder(Entity):
    parent_id: str = ""
    name: str = ""
    children: list["Folder"] = field(default_factory=list)


forge = registry()
workspace_table = entity_table(
    "workspace", forge.metadata, Column("code", Text, nullable=False)
)
code_repo_table = entity_table(
    "code_repo",
    forge.metadata,
    Column("workspace_id", Text, ForeignKey("workspace.id"), nullable=False),
    Column("name", Text, nullable=False),
)
check_table = entity_table(
    "check_run",
    forge.metadata,
    Column("repo_id", Text, ForeignKey("code_repo.id"), nullable=False),
    Column("name", Text, nullable=False),
)
note_table = entity_table(
    "note",
    forge.metadata,
    Column("workspace_id", Text, ForeignKey("workspace.id"), nullable=True),
    Column("text", Text, nullable=False),
)
member_table = entity_table(
    "member",
    forge.metadata,
    Column("workspace_id", Text, ForeignKey("workspace.id"), nullable=False),
    Column("name", Text, nullable=False),
)
doc_table = entity_table(
    "doc",
    forge.metadata,
    Column("workspace_id", Text, ForeignKey("workspace.id"), nullable=False),
    Column("title", Text, nullable=False),
    *archive_columns(),
)

issue_table = entity_table(
    "issue",
    forge.metadata,
    Column("workspace_id", Text, ForeignKey("workspace.id"), nullable=False),
    Column("state", Text, nullable=False),
)


task_table = entity_table(
    "task",
    forge.metadata,
    Column("workspace_id", Text, ForeignKey("workspace.id"), nullable=True),
    Column("title", Text, nullable=False),
)
tag_table = entity_table("tag", forge.metadata, Column("label", Text, nullable=False))
label_table = entity_table(
    "label",
    forge.metadata,
    Column("label", Text, nullable=False),
    Column("text", Text, nullable=False),
)
folder_table = entity_table(
    "folder",
    forge.metadata,
    Column("parent_id", Text, ForeignKey("folder.id"), nullable=False),
    Column("name", Text, nullable=False),
)


def declare() -> None:
    map_aggregates(
        forge,
        {
            Workspace: workspace_table,
            CodeRepo: code_repo_table,
            Check: check_table,
            Note: note_table,
            Member: member_table,
            Doc: doc_table,
            Issue: issue_table,
            Tag: tag_table,
            Label: label_table,
            Folder: folder_table,
            Task: task_table,
        },
        relations={
            Workspace: {
                "repositories": Relation.foreign_key(
                    CodeRepo, identified_by="workspace_id", orphans=Orphans.DELETE
                ),
                "notes": Relation.foreign_key(
                    Note, identified_by="workspace_id", orphans=Orphans.DETACH
                ),
                "docs": Relation.foreign_key(
                    Doc, identified_by="workspace_id", orphans=Orphans.DELETE
                ),
                "members": Relation.foreign_key(
                    Member, identified_by="workspace_id", owned=False
                ),
                "open_issues": Relation.foreign_key(
                    Issue,
                    identified_by="workspace_id",
                    orphans=Orphans.DELETE,
                    scope=Criteria(where=Condition(field="state", value="open")),
                ),
            },
            Tag: {
                "labels": Relation.foreign_key(
                    Label, parent_field="label", related_field="label"
                )
            },
            Folder: {
                "children": Relation.foreign_key(
                    Folder, identified_by="parent_id", orphans=Orphans.DELETE
                )
            },
            CodeRepo: {
                "checks": Relation.foreign_key(
                    Check, identified_by="repo_id", orphans=Orphans.DELETE
                )
            },
        },
    )
