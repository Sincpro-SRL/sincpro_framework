"""`EntityID`: a key that says whose record it names by its type — open by default, so typing a
`str` key changes nothing a caller may send; `EntityID[E, UUID]` for a standard UUID, read and
written as pydantic does; `StrictEntityID` when it must be the framework's UUID v7, read by
pydantic's own `UUID7` and kept as 32 hex; a key of another context's entity through a
`Reference` named in `shared/`.
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
    FieldType,
    Operator,
    Reference,
    StrictEntityID,
    apply_values,
)
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd.metamodel import StoreFacts, contract_of, description_of


@dataclass(kw_only=True)
class Project(Entity):
    name: str = ""


@dataclass(kw_only=True)
class Partner(Entity):
    """An entity whose records another system keys by a whole number — an Odoo partner."""

    name: str = ""


class Tenant(Reference, context="auth"):
    """auth's tenant, named once in `shared/`: a key points at it without importing auth."""


@dataclass(kw_only=True)
class Device(Entity):
    """An entity another system keys by a standard UUID."""

    serial: str = ""


@dataclass(kw_only=True)
class Issue(Entity):
    title: str = ""
    project_id: EntityID[Project] | None = None
    partner_id: EntityID[Partner, int] | None = None
    tenant_id: EntityID[Tenant] | None = None
    reviewer_id: StrictEntityID[Project] | None = None
    device_id: EntityID[Device, UUID] | None = None


class CommandMoveIssue(DataTransferObject):
    issue: EntityID[Issue]
    destination: EntityID[Project]


class CommandBill(DataTransferObject):
    partner: EntityID[Partner, int]


class CommandApprove(DataTransferObject):
    approver: StrictEntityID[Project]


class CommandJoin(DataTransferObject):
    tenant: EntityID[Tenant]


class CommandPair(DataTransferObject):
    device: EntityID[Device, UUID]


def _dashed(identity: str) -> str:
    """A UUID written with dashes and in capitals, as another library writes it."""
    return str(uuid.UUID(identity)).upper()


# ---------------------------------------------------------------------------------------------
# What a key takes
# ---------------------------------------------------------------------------------------------


def test_a_key_takes_whatever_the_plain_type_it_holds_takes():
    minted = new_entity_id()

    moved = CommandMoveIssue(issue="ana@acme.com", destination=minted)
    billed = CommandBill(partner="42")  # type: ignore[arg-type]  # a number written as text

    assert moved.issue == "ana@acme.com"  # a key of its own: an email, a slug
    assert moved.destination == minted
    assert billed.partner == 42
    with pytest.raises(ValidationError):
        CommandBill(partner="forty-two")  # type: ignore[arg-type]


def test_a_text_key_takes_a_uuid_as_the_framework_writes_it_and_any_text_as_it_came():
    minted = new_entity_id()

    taken = CommandMoveIssue(issue=UUID(minted), destination=_dashed(minted))  # type: ignore[arg-type]

    assert taken.issue == minted  # a `uuid.UUID`: its 32 hex
    assert taken.destination == _dashed(minted)  # a text key rewrites no text: it is open


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


def test_a_strict_key_takes_a_uuid_v7_in_any_spelling_and_keeps_the_frameworks_32_hex():
    minted = new_entity_id()

    for written in (minted, _dashed(minted), UUID(minted)):
        assert CommandApprove(approver=written).approver == minted  # type: ignore[arg-type]
    assert CommandApprove(approver=_dashed(minted)).model_dump_json() == (
        f'{{"approver":"{minted}"}}'
    )


def test_a_strict_key_refuses_what_pydantics_uuid7_refuses():
    refusals = {
        uuid.uuid4().hex: "uuid_version",
        str(uuid.uuid4()): "uuid_version",
        "1": "uuid_parsing",
        "ana@acme.com": "uuid_parsing",
    }
    for refused, kind in refusals.items():
        with pytest.raises(ValidationError) as error:
            CommandApprove(approver=refused)
        assert error.value.errors()[0]["type"] == kind


def test_a_strict_key_outside_a_model_is_read_as_pydantic_reads_any_type():
    strict = TypeAdapter(StrictEntityID[Project])
    minted = new_entity_id()

    assert strict.validate_python(_dashed(minted)) == minted
    assert strict.validate_python(UUID(minted)) == minted
    assert strict.validate_python(new_entity_id())  # every id the framework mints is one
    with pytest.raises(ValidationError):
        strict.validate_python(uuid.uuid4().hex)


def test_a_key_says_whose_it_is_beside_what_it_holds():
    moved = CommandMoveIssue.model_json_schema()["properties"]
    billed = CommandBill.model_json_schema()["properties"]["partner"]

    assert moved["destination"] == {
        "title": "Destination",
        "type": "string",
        "x-sincpro": {"relation": "Project"},
    }
    assert billed["type"] == "integer"
    assert billed["x-sincpro"] == {"relation": "Partner"}
    assert "pattern" not in moved["destination"]  # open: nothing a client should refuse


def test_a_strict_key_publishes_the_pattern_any_validator_checks_never_format_uuid():
    jsonschema = pytest.importorskip("jsonschema")
    schema = CommandApprove.model_json_schema()["properties"]["approver"]
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    )

    assert "format" not in schema  # `format: uuid` asks for dashes: it refuses our own ids
    assert validator.is_valid(new_entity_id())
    assert not validator.is_valid(uuid.uuid4().hex)


def test_a_key_of_another_contexts_entity_names_its_context_without_importing_it():
    schema = CommandJoin.model_json_schema()["properties"]["tenant"]

    assert schema["x-sincpro"] == {"relation": "auth.Tenant"}


# ---------------------------------------------------------------------------------------------
# A key on an entity
# ---------------------------------------------------------------------------------------------


def test_a_key_on_an_entity_is_filtered_as_what_it_holds_and_names_whose_it_is():
    fields = description_of(Issue, StoreFacts(identity="id")).fields

    assert fields["project_id"].type is FieldType.TEXT  # not `unknown`: filtered by value
    assert fields["project_id"].relation == "Project"
    assert Operator.EQ in fields["project_id"].ops and Operator.IN in fields["project_id"].ops
    assert fields["partner_id"].type is FieldType.INTEGER
    assert fields["partner_id"].relation == "Partner"
    assert fields["tenant_id"].relation == "auth.Tenant"
    assert fields["device_id"].type is FieldType.UUID
    assert fields["device_id"].relation == "Device"
    assert not fields["project_id"].navigable  # a key, not a relation a filter may cross


def test_a_key_on_an_entity_is_published_as_what_it_holds_never_as_the_entity():
    properties = contract_of(Issue)["properties"]

    assert properties["project_id"]["anyOf"][0] == {"type": "string"}
    assert properties["project_id"]["x-sincpro"]["relation"] == "Project"
    assert properties["partner_id"]["anyOf"][0] == {"type": "integer"}
    assert properties["tenant_id"]["x-sincpro"]["relation"] == "auth.Tenant"
    assert properties["device_id"]["anyOf"][0] == {"type": "string", "format": "uuid"}
    assert "Project" not in contract_of(Issue).get("$defs", {})


def test_a_form_keeps_a_key_of_its_own_and_refuses_a_strict_one_that_is_no_uuid_v7():
    issue = Issue()

    apply_values(issue, {"project_id": "acme-site", "partner_id": "7"})
    assert (issue.project_id, issue.partner_id) == ("acme-site", 7)
    with pytest.raises(ddd_exceptions.InvalidValue):
        apply_values(issue, {"reviewer_id": uuid.uuid4().hex})
    apply_values(issue, {"reviewer_id": _dashed(minted := new_entity_id())})
    assert issue.reviewer_id == minted


# ---------------------------------------------------------------------------------------------
# A mistake is refused where it is written
# ---------------------------------------------------------------------------------------------


def test_a_key_of_something_that_is_no_entity_is_refused_when_the_command_is_declared():
    with pytest.raises(ddd_exceptions.ProgrammingError, match="names <class 'int'>"):

        class CommandWrong(DataTransferObject):
            amount: EntityID[int]

    with pytest.raises(ddd_exceptions.ProgrammingError, match="holds <class 'float'>"):

        class CommandWorse(DataTransferObject):
            share: EntityID[Project, float]

    with pytest.raises(ddd_exceptions.ProgrammingError, match="a strict one the framework"):

        class CommandStrictNumber(DataTransferObject):
            partner: StrictEntityID[Partner, int]  # type: ignore[type-arg]


def test_a_reference_that_names_no_context_is_refused():
    with pytest.raises(ddd_exceptions.ProgrammingError, match="name that context"):

        class Orphan(Reference):
            pass
