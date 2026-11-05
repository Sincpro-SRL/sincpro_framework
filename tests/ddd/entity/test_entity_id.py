"""`EntityID` and `EntityKey`: a key that says whose record it names by its type. `EntityID` is the
framework's id — always a UUID v7, read by pydantic's own `UUID7` and kept as 32 hex —;
`EntityKey` a context's own key — a text, a whole number or a standard UUID, read and written as
pydantic reads and writes each —; another context's entity through a `Reference` named in
`shared/`.
"""

import uuid
from dataclasses import dataclass
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from sincpro_framework import DataTransferObject
from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.ddd import (
    Entity,
    EntityID,
    EntityKey,
    FieldType,
    Operator,
    Reference,
    apply_values,
)
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd.metamodel import StoreFacts, contract_of, description_of


@dataclass(kw_only=True)
class Project(Entity):
    name: str = ""


@dataclass(kw_only=True)
class User(Entity):
    """An entity a context keys by its own text — an email."""

    name: str = ""


@dataclass(kw_only=True)
class Partner(Entity):
    """An entity another system keys by a whole number — an Odoo partner."""

    name: str = ""


@dataclass(kw_only=True)
class Device(Entity):
    """An entity another system keys by a standard UUID."""

    serial: str = ""


class Tenant(Reference, context="auth"):
    """auth's tenant, named once in `shared/`: a key points at it without importing auth."""


@dataclass(kw_only=True)
class Issue(Entity):
    title: str = ""
    project_id: EntityID[Project] | None = None
    tenant_id: EntityID[Tenant] | None = None
    owner_id: EntityKey[User] | None = None
    partner_id: EntityKey[Partner, int] | None = None
    device_id: EntityKey[Device, UUID] | None = None


class CommandMoveIssue(DataTransferObject):
    issue: EntityID[Issue]
    destination: EntityID[Project]


class CommandAssign(DataTransferObject):
    owner: EntityKey[User]


class CommandBill(DataTransferObject):
    partner: EntityKey[Partner, int]


class CommandPair(DataTransferObject):
    device: EntityKey[Device, UUID]


class CommandJoin(DataTransferObject):
    tenant: EntityID[Tenant]


def _dashed(identity: str) -> str:
    """A UUID written with dashes and in capitals, as another library writes it."""
    return str(uuid.UUID(identity)).upper()


# ---------------------------------------------------------------------------------------------
# The framework's id: always a UUID v7, 32 hex
# ---------------------------------------------------------------------------------------------


def test_an_entity_id_takes_a_uuid_v7_in_any_spelling_and_keeps_the_frameworks_32_hex():
    minted = new_entity_id()

    for written in (minted, _dashed(minted), UUID(minted)):
        moved = CommandMoveIssue(issue=written, destination=minted)  # type: ignore[arg-type]
        assert moved.issue == minted
    assert CommandMoveIssue(issue=_dashed(minted), destination=minted).model_dump_json() == (
        f'{{"issue":"{minted}","destination":"{minted}"}}'
    )


def test_an_entity_id_refuses_what_pydantics_uuid7_refuses():
    refusals = {
        uuid.uuid4().hex: "uuid_version",
        str(uuid.uuid4()): "uuid_version",
        "1": "uuid_parsing",
        "acme-site": "uuid_parsing",
    }
    for refused, kind in refusals.items():
        with pytest.raises(ValidationError) as error:
            CommandMoveIssue(issue=refused, destination=new_entity_id())
        assert error.value.errors()[0]["type"] == kind


def test_every_id_the_framework_mints_is_an_entity_id_read_as_pydantic_reads_any_type():
    read = TypeAdapter(EntityID[Project])
    minted = new_entity_id()

    assert read.validate_python(minted) == minted
    assert read.validate_python(UUID(minted)) == minted
    with pytest.raises(ValidationError):
        read.validate_python(uuid.uuid4().hex)


def test_an_entity_id_publishes_the_pattern_any_validator_checks_never_format_uuid():
    jsonschema = pytest.importorskip("jsonschema")
    schema = CommandMoveIssue.model_json_schema()["properties"]["destination"]
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    )

    assert "format" not in schema  # `format: uuid` asks for dashes: it refuses our own ids
    assert schema["x-sincpro"] == {"relation": "Project"}
    assert validator.is_valid(new_entity_id())
    assert not validator.is_valid(uuid.uuid4().hex)


def test_a_key_of_another_contexts_entity_names_its_context_without_importing_it():
    schema = CommandJoin.model_json_schema()["properties"]["tenant"]

    assert schema["x-sincpro"] == {"relation": "auth.Tenant"}


# ---------------------------------------------------------------------------------------------
# A context's own key: what the plain value it holds takes
# ---------------------------------------------------------------------------------------------


def test_a_text_key_takes_any_text_and_a_uuid_as_the_frameworks_32_hex():
    minted = new_entity_id()

    assert CommandAssign(owner="ana@acme.com").owner == "ana@acme.com"
    assert CommandAssign(owner=UUID(minted)).owner == minted  # type: ignore[arg-type]
    assert CommandAssign(owner=_dashed(minted)).owner == _dashed(minted)  # no text rewritten
    assert CommandAssign.model_json_schema()["properties"]["owner"] == {
        "title": "Owner",
        "type": "string",
        "x-sincpro": {"relation": "User"},
    }


def test_an_integer_key_takes_what_an_int_field_takes():
    assert CommandBill(partner="42").partner == 42  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        CommandBill(partner="forty-two")  # type: ignore[arg-type]
    assert CommandBill.model_json_schema()["properties"]["partner"]["type"] == "integer"


def test_a_standard_uuid_key_is_read_and_written_as_pydantic_does():
    minted = new_entity_id()

    for written in (minted, _dashed(minted), UUID(minted)):
        assert CommandPair(device=written).device == UUID(minted)  # type: ignore[arg-type]
    assert CommandPair(device=UUID(minted)).model_dump_json() == (
        f'{{"device":"{UUID(minted)}"}}'  # the standard's 36, with dashes
    )
    assert CommandPair.model_json_schema()["properties"]["device"] == {
        "title": "Device",
        "type": "string",
        "format": "uuid",
        "x-sincpro": {"relation": "Device"},
    }


# ---------------------------------------------------------------------------------------------
# A key on an entity
# ---------------------------------------------------------------------------------------------


def test_a_key_on_an_entity_is_filtered_as_what_it_holds_and_names_whose_it_is():
    fields = description_of(Issue, StoreFacts(identity="id")).fields

    assert fields["project_id"].type is FieldType.TEXT  # not `unknown`: filtered by value
    assert fields["project_id"].relation == "Project"
    assert Operator.EQ in fields["project_id"].ops and Operator.IN in fields["project_id"].ops
    assert fields["tenant_id"].relation == "auth.Tenant"
    assert (fields["owner_id"].type, fields["owner_id"].relation) == (FieldType.TEXT, "User")
    assert (fields["partner_id"].type, fields["partner_id"].relation) == (
        FieldType.INTEGER,
        "Partner",
    )
    assert (fields["device_id"].type, fields["device_id"].relation) == (
        FieldType.UUID,
        "Device",
    )
    assert not fields["project_id"].navigable  # a key, not a relation a filter may cross


def test_a_key_on_an_entity_is_published_as_what_it_holds_never_as_the_entity():
    properties = contract_of(Issue)["properties"]

    assert properties["project_id"]["anyOf"][0] == {"type": "string"}
    assert properties["project_id"]["x-sincpro"]["relation"] == "Project"
    assert properties["partner_id"]["anyOf"][0] == {"type": "integer"}
    assert properties["device_id"]["anyOf"][0] == {"type": "string", "format": "uuid"}
    assert properties["tenant_id"]["x-sincpro"]["relation"] == "auth.Tenant"
    assert "Project" not in contract_of(Issue).get("$defs", {})


def test_a_form_keeps_a_contexts_own_key_and_refuses_an_id_that_is_no_uuid_v7():
    issue = Issue()
    minted = new_entity_id()

    apply_values(issue, {"owner_id": "ana@acme.com", "partner_id": "7"})
    assert (issue.owner_id, issue.partner_id) == ("ana@acme.com", 7)
    with pytest.raises(ddd_exceptions.InvalidValue):
        apply_values(issue, {"project_id": uuid.uuid4().hex})
    apply_values(issue, {"project_id": _dashed(minted)})
    assert issue.project_id == minted


# ---------------------------------------------------------------------------------------------
# A mistake is refused where it is written
# ---------------------------------------------------------------------------------------------


def test_a_key_of_something_that_is_no_entity_is_refused_when_the_command_is_declared():
    with pytest.raises(ddd_exceptions.ProgrammingError, match="names <class 'int'>"):

        class CommandWrong(DataTransferObject):
            amount: EntityID[int]

    with pytest.raises(ddd_exceptions.ProgrammingError, match="holds <class 'float'>"):

        class CommandWorse(DataTransferObject):
            share: EntityKey[Project, float]

    with pytest.raises(ddd_exceptions.ProgrammingError, match="UUID v7 alone"):

        class CommandNumberedId(DataTransferObject):
            partner: EntityID[Partner, int]  # type: ignore[type-arg]


def test_a_reference_that_names_no_context_is_refused():
    with pytest.raises(ddd_exceptions.ProgrammingError, match="name that context"):

        class Orphan(Reference):
            pass
