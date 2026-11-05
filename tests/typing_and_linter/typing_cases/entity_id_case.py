from dataclasses import dataclass
from typing import assert_type
from uuid import UUID

from sincpro_framework import DataTransferObject
from sincpro_framework.ddd import Entity, EntityID, EntityKey, Reference


@dataclass(kw_only=True)
class Project(Entity):
    name: str = ""


@dataclass(kw_only=True)
class Partner(Entity):
    name: str = ""


@dataclass(kw_only=True)
class Device(Entity):
    serial: str = ""


class Tenant(Reference, context="auth"):
    """auth's tenant, named in `shared/`."""


@dataclass(kw_only=True)
class Issue(Entity):
    project_id: EntityID[Project] | None = None
    partner_id: EntityKey[Partner, int] | None = None


class CommandMoveIssue(DataTransferObject):
    issue: EntityID[Issue]
    destination: EntityID[Project]
    owner: EntityKey[Project]
    partner: EntityKey[Partner, int]
    device: EntityKey[Device, UUID]
    tenant: EntityID[Tenant]
    key: EntityID


def verify_a_key_is_the_value_it_holds_in_the_editor(issue: Issue) -> None:
    """Any `str` is an entity's id — its own id above all — or a text key, and the type named
    second is what a context's key holds."""
    command = CommandMoveIssue(
        issue=issue.id,
        destination=issue.id,
        owner="acme-site",
        partner=42,
        device=UUID(int=7),
        tenant=issue.id,
        key=issue.id,
    )

    assert_type(command.issue, str)
    assert_type(command.partner, int)
    assert_type(command.device, UUID)
    assert_type(command.owner, str)
    assert_type(command.key, str)
    assert_type(issue.partner_id, int | None)
    command.destination.upper()
    print(command.partner + 1)
