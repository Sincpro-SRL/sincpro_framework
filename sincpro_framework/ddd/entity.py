"""What every aggregate needs in order to live: an identity, and when it was born and touched.

    @dataclass
    class Note(Entity):
        title: str
        body: str = ""

    Note(title="hello")           →  id='0192…' (uuid v7), created_at=now, version=0

A base class rather than a convention, so a table gets the same four columns everywhere and
the engine can stamp `updated_at` and check `version` without being told which fields those
are. Everything else about an aggregate is its own business.

**The id is a UUID v7 written as 32 hex characters.** Sortable, because its leading bits are
a millisecond timestamp — ordering by id *is* ordering by creation, which is what makes the
engine's default order meaningful with no second column. And local to the B-tree, because
inserts append instead of scattering. A project that prefixes its ids (`ds_…`) passes the id
in; the default is for the aggregate that has no such convention.

**`version` starts at zero and means «never persisted».** The adapter counts from one on the
first insert and raises it on every write, refusing a save that carries an older number than
the row holds — two callers that both loaded version 3 cannot both write version 4. That is
the conditional update a worker needs, and it is the ORM's own machinery doing it.

Python 3.12 and 3.13 have no `uuid.uuid7`; `uuid7()` below is RFC 9562 by hand and defers to
the standard library where it exists.
"""

import dataclasses
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from pydantic import TypeAdapter

if TYPE_CHECKING:
    from sincpro_framework.ddd.events import DomainEvent

RECORDED = "_recorded_events"


@lru_cache(maxsize=None)
def json_serializer(cls: type) -> TypeAdapter:
    return TypeAdapter(cls)


_MINTING = threading.Lock()
_last_millisecond = 0
_counter = 0


def uuid7() -> uuid.UUID:
    """A time-ordered UUID (RFC 9562 version 7).

        layout  48 bits unix milliseconds · 4 bits version · 12 bits counter within the ms
                2 bits variant · 62 bits random

    >>> uuid7().version
    7
    """
    native = getattr(uuid, "uuid7", None)
    if native is not None:
        return native()

    milliseconds = time.time_ns() // 1_000_000
    entropy = int.from_bytes(os.urandom(10), "big")
    rand_b = entropy & ((1 << 62) - 1)
    with _MINTING:
        # RFC 9562 method 1: within one millisecond the 12 bits are a counter, so ids minted
        # in order sort in order; a new millisecond starts the counter at a random point.
        global _last_millisecond, _counter
        if milliseconds == _last_millisecond and _counter < 0xFFF:
            _counter += 1
        else:
            _last_millisecond = milliseconds
            _counter = (entropy >> 62) & 0x7FF
        rand_a = _counter
    value = (milliseconds << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def new_entity_id() -> str:
    """The id a fresh entity gets when nobody passes one.

    >>> len(new_entity_id())
    32
    """
    return uuid7().hex


def utc_now() -> datetime:
    """Aware, in UTC. A naive timestamp is one somebody later reads in the wrong zone."""
    return datetime.now(UTC)


@dataclass(kw_only=True)
class Audited:
    """Who wrote the record, beside when: a mixin an aggregate opts into.

        @dataclass
        class Invoice(Audited, Entity):
            number: str

    Nobody writes these two. The adapter stamps them on every flush from the actor the
    `Database` was given — usually `lambda: bus.context.get("user.id")`, the same id the bus
    already carries through the call. Without an actor they stay `None`, and the aggregate
    works unchanged.

    The columns go on the table with `audit_columns()`, beside `entity_columns()`.
    """

    created_by: str | None = None
    updated_by: str | None = None


@dataclass(kw_only=True)
class Archivable:
    """Put away rather than deleted: a mixin an aggregate opts into.

        @dataclass
        class Account(Archivable, Entity):
            code: str

        repository.remove(account)          →  archived_at stamped, the row stays
        repository.purge(account)           →  DELETE, when it really has to go
        repository.search(Accounts)         →  the live ones
        Criteria(where=Condition(field="archived_at", operator=Operator.IS_NULL, value=False))
                                            →  the archived ones, asked for by name

    **A read leaves the archived out unless the criteria names `archived_at`.** What a business
    calls deleting is almost always this: the record has to stop appearing and cannot be lost,
    because invoices point at it. Odoo spells it `active`; the column here says when.

    The column goes on the table with `archive_columns()`.
    """

    archived_at: datetime | None = None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def archive(self) -> None:
        """Puts the record away. Already archived, nothing moves: the moment it left is the
        first one, not the last time somebody asked again."""
        if self.archived_at is None:
            self.archived_at = utc_now()

    def restore(self) -> None:
        self.archived_at = None


class Translated(TypedDict):
    """Every word a screen needs for an aggregate, what `Entity.translations()` answers:

        {"name": {"default": "Note"},
         "labels": {"title": {"default": "Title"}},
         "help": {"title": {"default": "What the note is about"}}}

    Every text is `{"default": …}` plus whatever languages the project speaks.
    """

    name: dict[str, str]
    labels: dict[str, dict[str, str]]
    help: NotRequired[dict[str, dict[str, str]]]


@dataclass(kw_only=True)
class Entity:
    """The four fields every aggregate carries, as keyword-only defaults so a subclass keeps
    declaring its own fields positionally and without defaults — and one class method that
    says how a screen names the aggregate and each of its fields.

        @dataclass
        class Note(Entity):
            title: str

            @classmethod
            def translations(cls) -> Translated:
                return {"name": {"default": "Note"}, "labels": {"title": {"default": "Title"}}}

    A subclass is an ordinary `@dataclass`. Its first declared field is still its identity,
    because these come first in the field order — which is the convention `EntityCollection`
    reads identity by. `translations()` is read once per class into the definition.
    """

    id: str = field(default_factory=new_entity_id)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime | None = None
    """When it was last written. `None` until the first update; the adapter stamps it on
    every flush that changed something, so nobody remembers to."""
    version: int = 0
    """How many times it has been written. Zero means never — the adapter raises it, and a
    save carrying a number older than the row's is refused as `StaleAggregate`."""

    @classmethod
    def translations(cls) -> Translated:
        """Every word a screen shows for this aggregate, in one dictionary. Override it.

            {"name": {"default": "Note"},
             "labels": {"title": {"default": "Title"}},
             "help": {"title": {"default": "What the note is about"}}}

        Read by the definition of the model, once per class.
        """
        return {"name": {"default": cls.__name__}, "labels": {}}

    @property
    def is_new(self) -> bool:
        """Whether this entity has never reached the storage.

        >>> Note(title="x").is_new
        True
        """
        return self.version == 0

    def record(self, event: "DomainEvent") -> "DomainEvent":
        """Says that something happened to this entity, without telling anybody yet.

            note.record(NoteArchived(reason="stale"))
            →  the event carries this entity's type and id, and its place in the sequence

        Publishing is somebody else's job: the Feature that saved the change pulls what was
        recorded and hands it to an event bus, explicitly. These live in memory and die with
        the object — nothing here stores them. An entity that published directly would
        announce facts a rollback then undoes.
        """
        recorded: list[DomainEvent] = self.__dict__.setdefault(RECORDED, [])
        stamped = dataclasses.replace(
            event,
            entity_type=event.entity_type or type(self).__name__,
            entity_id=event.entity_id or self.id,
            sequence=len(recorded),
        )
        recorded.append(stamped)
        return stamped

    def pull_events(self) -> list["DomainEvent"]:
        """Everything recorded since the last pull, in order — and nothing afterwards.

        >>> note.record(NoteArchived(...)); note.pull_events()
        [NoteArchived(...)]
        >>> note.pull_events()
        []
        """
        recorded = self.__dict__.pop(RECORDED, [])
        return list(recorded)

    def as_json(self) -> str:
        """This entity as JSON text.

        >>> Note(title="x").as_json()
        '{"id":"...","created_at":"...","title":"x"}'
        """
        return json_serializer(type(self)).dump_json(self).decode()

    @classmethod
    def from_json(cls, data: "str | dict[str, Any]") -> "Any":
        """Rebuilds an instance from JSON text or an already-parsed dict — either is
        accepted, so a caller that already has the dict (a DB row, another service's
        response) never has to round-trip it through a string first.

        >>> Note.from_json('{"title": "x"}')
        Note(title='x')
        >>> Note.from_json({"title": "x"})
        Note(title='x')
        """
        adapter = json_serializer(cls)
        return (
            adapter.validate_json(data)
            if isinstance(data, str)
            else adapter.validate_python(data)
        )
