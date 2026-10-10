"""A foreign key to a unique column that is not the primary key: a business key.

workspace.code          unique, what people read and what the child points at
repository.workspace_code  ForeignKey("workspace.code"), not "workspace.id"
Workspace.repositories  a one2many inferred from that key
Repository.workspace    the same key, seen as a many2one
"""

from dataclasses import dataclass, field

from sqlalchemy import Column, ForeignKey, Text
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm import map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.ddd.entity import Entity


@dataclass
class Workspace(Entity):
    code: str = ""
    repositories: list["CodeRepository"] = field(default_factory=list)


@dataclass
class CodeRepository(Entity):
    workspace_code: str = ""
    name: str = ""
    workspace: Workspace | None = None


forge = registry()
workspace_table = entity_table(
    "workspace", forge.metadata, Column("code", Text, nullable=False, unique=True)
)
code_repository_table = entity_table(
    "code_repository",
    forge.metadata,
    Column("workspace_code", Text, ForeignKey("workspace.code"), nullable=False),
    Column("name", Text, nullable=False),
)


def declare() -> None:
    map_aggregates(forge, {Workspace: workspace_table, CodeRepository: code_repository_table})
