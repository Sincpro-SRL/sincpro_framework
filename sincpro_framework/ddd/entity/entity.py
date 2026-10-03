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

`uuid7()` and `new_entity_id()` live in `sincpro_framework.ids` — the same ids an event and an
execution get — and are named here as they always were.

**`AuditedMixin`, `ArchivableMixin` and `ChangeTrackingMixin` live in `entity/mixins/`** — an
aggregate opts into each independently, the same way it opts into this base class.
"""

import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from sincpro_framework.context.infrastructure.tree import chain_for
from sincpro_framework.ids import new_entity_id

if TYPE_CHECKING:
    from sincpro_framework.ddd.events import DomainEvent

RECORDED = "_recorded_events"


@lru_cache(maxsize=None)
def json_serializer(cls: type) -> TypeAdapter:
    return TypeAdapter(cls)


def utc_now() -> datetime:
    """Aware, in UTC. A naive timestamp is one somebody later reads in the wrong zone."""
    return datetime.now(UTC)


Translated = dict[str, str]
"""One piece of text in every language the project speaks, `{"default": "Note", "es": "Nota"}`
— always a `"default"`, plus whatever languages beyond it. The shape `Entity.translations()`
answers for the aggregate's own name, and the same shape `field(metadata={"label": …, "help":
…})` uses for one field. A field's label and help are never in `translations()` — they live on
the field itself, read by `describe_class`/`describe` straight off the dataclass, because they
are a property of that field, not of the class."""


@dataclass(kw_only=True)
class Entity:
    """The four fields every aggregate carries, as keyword-only defaults so a subclass keeps
    declaring its own fields positionally and without defaults — and one class method that
    says how a screen names the aggregate.

        @dataclass
        class Note(Entity):
            title: str = field(metadata={"label": {"default": "Title"}})

            @classmethod
            def translations(cls) -> Translated:
                return {"default": "Note"}

    A subclass is an ordinary `@dataclass`. Its first declared field is still its identity,
    because these come first in the field order — which is the convention `EntityCollection`
    reads identity by. `translations()` is read once per class into the definition; a field's
    own label and help are read off `field(metadata=...)`, not from here.
    """

    id: str = field(default_factory=new_entity_id)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime | None = None
    """When it was last written. `None` until it is first stored, when it is `created_at`; the
    adapter stamps it on every flush that changed something, so nobody remembers to — and
    "untouched since" is this one column."""
    version: int = 0
    """How many times it has been written. Zero means never — the adapter raises it, and a
    save carrying a number older than the row's is refused as `StaleAggregate`."""

    @classmethod
    def translations(cls) -> Translated:
        """The aggregate's own name, in every language the project speaks. Override it.

            {"default": "Note", "es": "Nota"}

        Read by the definition of the model, once per class. A field's own label and help text
        are not here — see `field(metadata={"label": …, "help": …})`.
        """
        return {"default": cls.__name__}

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
            →  the event carries this entity's type and id

        Publishing is somebody else's job: the Feature that saved the change pulls what was
        recorded and hands it to an event bus, explicitly. These live in memory and die with
        the object — nothing here stores them. An entity that published directly would
        announce facts a rollback then undoes.

        Recorded inside an execution, the event is caused by it and joins its flow
        (`causation_id`, `correlation_id`) unless it already says otherwise.
        """
        recorded: list[DomainEvent] = self.__dict__.setdefault(RECORDED, [])
        stamped = dataclasses.replace(
            event,
            entity_type=event.entity_type or type(self).__name__,
            entity_id=event.entity_id or self.id,
            **chain_for(event),
        )
        recorded.append(stamped)
        return stamped

    def recorded_events(self) -> tuple["DomainEvent", ...]:
        """What is recorded right now, **without taking it** — `pull_events` is what takes it.

        For looking while the work is still going on: an assertion in a test, a log line, or a
        store answering which fact it just wrote down.
        """
        return tuple(self.__dict__.get(RECORDED, []))

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
