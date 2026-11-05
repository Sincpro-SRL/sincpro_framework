"""`EntityID`: a key that says whose record it names — said by its type, never by the field's name.

    class CommandMoveIssue(DataTransferObject):
        issue: EntityID[Issue]                  an issue's key: whatever a `str` field takes
        destination: EntityID[Project]
        partner: EntityID[Partner, int]         a key that is a whole number
        device: EntityID[Device, UUID]          a standard UUID, as pydantic reads and writes one
        approver: StrictEntityID[User]          only a UUID v7, as the framework mints it

    @dataclass(kw_only=True)
    class Issue(Entity):
        project_id: EntityID[Project] | None = None
        tenant_id: EntityID[Tenant] | None = None      # Tenant: auth's, named in `shared/`

Open by default: an `EntityID` takes what the plain value it holds takes — a `str` unless a type
is named second —, so typing a key says whose it is and changes nothing a caller may send. A
context that keeps keys of its own — an email, a slug, an Odoo integer, a UUID of another
system — types them the same way. Each kind is read and written by pydantic's own rules:

| Annotation | Holds | Takes | Writes |
|---|---|---|---|
| `EntityID[E]` | `str` | any text; a `uuid.UUID` as its 32 hex | the text |
| `EntityID[E, int]` | `int` | what an `int` field takes | the number |
| `EntityID[E, UUID]` | `uuid.UUID` | any spelling of a UUID (pydantic's `UUID`) | 36 with dashes, `format: uuid` |
| `StrictEntityID[E]` | `str` | a UUID v7 in any spelling (pydantic's `UUID7`) | 32 lowercase hex, its pattern |

`StrictEntityID` is the framework's own id (`new_entity_id`): the same UUID v7 written with dashes,
in capitals or as a `uuid.UUID` is read into the one text every table, draft, event and client
holds; anything else is refused, as pydantic refuses it (`uuid_version`, `uuid_parsing`). The
framework writes 32 hex and not the standard's 36: changing it would change every id stored.
Outside a model a key is read the way pydantic reads any type, one reading — the field's:

    TypeAdapter(StrictEntityID[Issue]).validate_python(value)

What it says is published — `x-sincpro.relation` beside the key: the entity's name in its own
context, `auth.Tenant` for another's (`Reference`) — and read: an operation's `targets`, the
entities a use case concerns, the type a field is filtered as (`key_of`).

Context: a type checker reads the value the key holds — a `str`, or the type named second — so
any `str` is a valid `EntityID[Issue]` and an `EntityID[Partner, int]` is an `int` in the editor.
What runs is a class pydantic asks for its schema, whose arguments say the entity. A type checker
enforces no bound on an alias's parameter, so whose a key is is checked when its schema is built:
an `EntityID[int]` fails at import, naming what it got.
"""

import re
import types
import uuid
from dataclasses import dataclass
from operator import attrgetter
from typing import TYPE_CHECKING, Any, ClassVar, Union, get_args, get_origin

from pydantic_core import core_schema

from sincpro_framework.ddd import exceptions

UUID7 = re.compile(r"^[0-9a-f]{12}7[0-9a-f]{3}[89ab][0-9a-f]{15}$")
"""A UUID v7 as the framework writes it (`new_entity_id`): 32 lowercase hex, version 7, variant
`10` — the pattern a strict key is published with, for a client to check it by. pydantic reads
it (`uuid_schema(version=7)`); this only says it."""

KEY = "sincpro_key"
"""Where a key's core schema carries what it says, for its JSON Schema."""

HELD = (str, int, uuid.UUID)
"""What a key may hold: a text — the framework's 32 hex, a slug, an email —, a whole number, or a
standard UUID."""


class Reference:
    """An entity of another bounded context, named once where every context may read it
    (`shared/`), so a key says whose it is without importing that context.

        class Tenant(Reference, context="auth"):
            '''auth's tenant.'''

        tenant_id: EntityID[Tenant]       ->  published as `relation: "auth.Tenant"`

    It is no entity here — no table, no contract, no model of this context —: a name, and the
    context that owns it.
    """

    context: ClassVar[str] = ""

    def __init_subclass__(cls, context: str = "", **kwargs: Any) -> None:
        """Remember the context it belongs to; a reference that names none is refused."""
        super().__init_subclass__(**kwargs)
        if not context:
            raise exceptions.ProgrammingError(
                f"{cls.__name__} is another context's entity: name that context — "
                f'`class {cls.__name__}(Reference, context="auth")`.'
            )
        cls.context = context


@dataclass(frozen=True)
class Key:
    """What a key's annotation says: whose record it names — `None` for a bare `EntityID`, the
    key of the one entity an operation concerns —, what it holds, and whether it must be the
    framework's UUID v7."""

    entity: type | None
    held: type
    strict: bool

    @property
    def relation(self) -> str | None:
        """Whose key it is, as a contract names it: the entity's name in its own context,
        `auth.Tenant` for another's; `None` when it does not say.

            Key(Project, str, False).relation   ->  "Project"
            Key(Tenant, str, False).relation    ->  "auth.Tenant"
        """
        if self.entity is None:
            return None
        if issubclass(self.entity, Reference):
            return f"{self.entity.context}.{self.entity.__name__}"
        return self.entity.__name__


def key_of(annotation: Any) -> Key | None:
    """What a key's annotation says — its `| None` aside —; `None` for any other annotation.

        key_of(EntityID[Issue])               ->  Key(Issue, str, strict=False)
        key_of(EntityID[Partner, int])        ->  Key(Partner, int, strict=False)
        key_of(EntityID[Device, UUID])        ->  Key(Device, UUID, strict=False)
        key_of(StrictEntityID[Issue] | None)  ->  Key(Issue, str, strict=True)
        key_of(EntityID)                      ->  Key(None, str, strict=False)
        key_of(str)                           ->  None
        key_of(EntityID[int])                 ->  raises ProgrammingError

    1. The optional stripped: a key that may be absent is still a key.
    2. `EntityID` or `StrictEntityID`, bare or with arguments; anything else is no key.
    3. Whose it is — an entity, or another context's (`Reference`) — and what it holds — a
       `str`, an `int` or a `uuid.UUID` (`HELD`); a strict one, the framework's text.
    Final: the key; a mistake refused, naming what it got.
    """
    if get_origin(annotation) in (Union, types.UnionType):
        present = [one for one in get_args(annotation) if one is not type(None)]
        if len(present) == 1:
            annotation = present[0]
    origin = get_origin(annotation) or annotation
    if origin is not EntityID and origin is not StrictEntityID:
        return None
    arguments = get_args(annotation)
    entity = arguments[0] if arguments else None
    held = arguments[1] if len(arguments) > 1 else str
    strict = origin is StrictEntityID
    if entity is not None:
        # Imported when asked: `entity_meta` reads this module, and `entity` reads `entity_meta`.
        from sincpro_framework.ddd.entity.entity import Entity

        if not (isinstance(entity, type) and issubclass(entity, (Entity, Reference))):
            raise exceptions.ProgrammingError(
                f"{annotation} names {entity!r}: a key names an Entity, or another context's "
                f"entity through a `Reference`."
            )
    if held not in HELD or (strict and held is not str):
        raise exceptions.ProgrammingError(
            f"{annotation} holds {held!r}: a key holds a `str`, an `int` or a `uuid.UUID`, and "
            f"a strict one the framework's text alone."
        )
    return Key(entity, held, strict)


def _as_text(value: Any) -> Any:
    """A `uuid.UUID` as the framework writes it, 32 hex, for a text key to hold; anything else as
    it came — the text key reads it as a `str` field does."""
    return value.hex if isinstance(value, uuid.UUID) else value


def key_schema(source: Any) -> core_schema.CoreSchema:
    """How pydantic reads a key annotated `source`: as the value it holds, by pydantic's own
    rule for it, what the annotation says carried for its JSON Schema.

    1. What it says (`key_of`), refused at import when it is a mistake.
    2. A strict key: pydantic's own `UUID7` (`uuid_schema(version=7)`) — 32 hex, 36 with
       dashes, capitals or a `uuid.UUID`; version 7 and RFC 9562's variant, else refused as
       pydantic refuses it (`uuid_version`, `uuid_parsing`) —, held and sent as the
       framework's 32 lowercase hex.
    3. A `uuid.UUID`: pydantic's `UUID` — any spelling in, the standard's out.
    4. An `int`: as an `int` field.
    Final: a text: as a `str` field, a `uuid.UUID` read as its 32 hex (`_as_text`).
    """
    key = key_of(source) or Key(None, str, False)
    metadata = {KEY: key}
    if key.strict:
        return core_schema.no_info_after_validator_function(
            attrgetter("hex"),
            core_schema.uuid_schema(version=7),
            serialization=core_schema.to_string_ser_schema(),
            metadata=metadata,
        )
    if key.held is uuid.UUID:
        return core_schema.uuid_schema(metadata=metadata)
    if key.held is int:
        return core_schema.int_schema(metadata=metadata)
    return core_schema.no_info_before_validator_function(
        _as_text, core_schema.str_schema(), metadata=metadata
    )


def key_json_schema(schema: Any, written: dict[str, Any]) -> dict[str, Any]:
    """What a key publishes, beside what it holds: a strict one, the pattern of the framework's
    UUID v7 in place of `format: uuid` — which asks for the 36 with dashes it never writes —;
    whose key it is, under `x-sincpro.relation`.

        {"type": "string", "x-sincpro": {"relation": "Project"}}
        {"type": "string", "format": "uuid", "x-sincpro": {"relation": "Device"}}
        {"type": "string", "pattern": "^[0-9a-f]{12}7…$", "x-sincpro": {"relation": "auth.Tenant"}}
    """
    key = (schema.get("metadata") or {}).get(KEY)
    if key is None:
        return written
    if key.strict:
        written.pop("format", None)
        written["pattern"] = UUID7.pattern
    if key.relation is not None:
        written["x-sincpro"] = {**written.get("x-sincpro", {}), "relation": key.relation}
    return written


if TYPE_CHECKING:
    # A type checker reads the value a key holds: any `str` is an `EntityID[Issue]`, and the
    # type named second (`EntityID[Partner, int]`, `EntityID[Device, UUID]`) is what the
    # editor offers.
    from typing_extensions import TypeAliasType, TypeVar

    _Entity = TypeVar("_Entity")
    _Held = TypeVar("_Held", default=str)
    EntityID = TypeAliasType("EntityID", _Held, type_params=(_Entity, _Held))
    type StrictEntityID[E] = str
else:

    class EntityID(str):
        """The key of an entity's record: `EntityID[Issue]`, `EntityID[Partner, int]`,
        `EntityID[Device, UUID]`, or `EntityID` alone — the key of the one entity an operation
        concerns."""

        def __class_getitem__(cls, arguments: Any) -> Any:
            """`EntityID[Issue]` and `EntityID[Partner, int]` alike: what pydantic reads the
            entity and what the key holds off."""
            return types.GenericAlias(cls, arguments)

        @classmethod
        def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
            """Read as the value it holds (`key_schema`)."""
            return key_schema(source)

        @classmethod
        def __get_pydantic_json_schema__(cls, schema: Any, handler: Any) -> Any:
            """Published as what it holds, and whose key it is (`key_json_schema`)."""
            return key_json_schema(schema, handler(schema))

    class StrictEntityID(EntityID):
        """The key of an entity's record that only a UUID v7 may be, written as the framework
        writes it. Outside a model it is read as pydantic reads any type:
        `TypeAdapter(StrictEntityID[Issue]).validate_python(value)`."""
