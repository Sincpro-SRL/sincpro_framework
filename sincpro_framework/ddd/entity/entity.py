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

`uuid7()` and `new_entity_id()` live in `sincpro_framework.common.ids` — the same ids an event and an
execution get — and are named here as they always were.

**`AuditedMixin`, `ArchivableMixin` and `ChangeTrackingMixin` live in `entity/mixins/`** — an
aggregate opts into each independently, the same way it opts into this base class.
"""

import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import TypeAdapter

from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.context.infrastructure.tree import chain_for
from sincpro_framework.ddd.criteria import (
    TEXT,
    Condition,
    Criteria,
    Operator,
    Sort,
    Specification,
)
from sincpro_framework.ddd.entity.derivations import Derivations
from sincpro_framework.ddd.entity.presentation import Presentation
from sincpro_framework.ddd.entity.utils.annotations import is_class_var, own_annotations
from sincpro_framework.exceptions import ProgrammingError

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


_MEANING = {
    "id": " (the identity)",
    "created_at": " (stamped when first stored)",
    "updated_at": " (stamped on every write)",
    "version": " (the optimistic lock)",
}
_RENAMED = {"version": ", e.g. `revision`", "id": ", e.g. `code`"}

DEFAULT_HOOKS = (
    "DEFAULT_GET_ID",
    "DEFAULT_READING",
    "DEFAULT_ORDER",
    "DEFAULT_DISPLAY",
    "DEFAULT_LITERAL_SEARCH",
)
"""What an entity answers about how it is read, each a class method `Entity` defines and a
subclass may override — always `@classmethod`, the shape it overrides, so a type checker and
the framework agree. Upper case so it never meets a field, and so a reader knows the framework
calls it."""


def reserved_fields(declared: type) -> dict[str, str]:
    """The fields of `declared` the framework writes, each with the class that declares it.

    in      Spec(Entity)                        →  {"id": "Entity", …, "version": "Entity"}
    in      Client(ArchivableMixin, Entity)     →  … plus {"archived_at": "ArchivableMixin"}
    """
    found: dict[str, str] = {}
    for owner in declared.__mro__[1:]:
        if not owner.__module__.startswith("sincpro_framework."):
            continue
        if owner is not Entity and isinstance(owner, type) and issubclass(owner, Entity):
            continue
        for name, annotation in own_annotations(owner).items():
            if not is_class_var(annotation):
                found.setdefault(name, owner.__name__)
    return found


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

    How it is read is the entity's own answer, five class methods a subclass overrides:
    `DEFAULT_GET_ID` (the field `Get` finds it by), `DEFAULT_READING` (what one record
    brings), `DEFAULT_ORDER` (the list order), `DEFAULT_DISPLAY` (the field shown beside its
    identity) and `DEFAULT_LITERAL_SEARCH` (how a typed text finds it). They are defaults: a
    criteria a caller sends wins for every part it names.

    `presentation` says how a form shows each field — read-only, required, visible — as hints
    a client starts from (`Presentation`). It is a class attribute, not a field: it is never
    stored and never travels with a record.

    `derivations` lists the fields it computes from others (`Derivations`), so a preview and a
    save compute them the same way. A class attribute too; left alone, nothing is computed.
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
    presentation: ClassVar[Presentation[Any]] = Presentation()
    derivations: ClassVar[Derivations[Any]] = Derivations()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Refuse a subclass that redeclares a field the framework writes.

            class Spec(Entity):
                version: int = 3        →  ProgrammingError: Spec.version redeclares
                                           Entity.version, the optimistic lock …

        1. A framework class is not checked: `DomainEvent` restates `id` and `created_at`.
        2. The reserved fields are `Entity`'s own and those of a framework mixin in the class's
           bases (`archived_at`, `created_by`, …); a framework subclass of `Entity`, like
           `DomainEvent`, adds none — its envelope (`label`, `entity_type`) is set by its own
           subclasses on purpose.
        Final: a redeclared one is refused here, when the class is declared, instead of being
        taken over in silence at the first save.
        """
        super().__init_subclass__(**kwargs)
        if cls.__module__.startswith("sincpro_framework."):
            return
        annotated = own_annotations(cls)
        for hook in DEFAULT_HOOKS:
            declared = cls.__dict__.get(hook)
            if hook in annotated or (
                declared is not None and not isinstance(declared, classmethod)
            ):
                kind = "a field" if hook in annotated else type(declared).__name__
                raise ProgrammingError(
                    f"{cls.__name__}.{hook} is {kind}: it is a class method the framework "
                    f"calls. Declare it as `@classmethod def {hook}(cls) -> …`."
                )
        reserved = reserved_fields(cls)
        taken = [name for name in own_annotations(cls) if name in reserved]
        if taken:
            name = taken[0]
            raise ProgrammingError(
                f"{cls.__name__}.{name} redeclares {reserved[name]}.{name}, a field the "
                f"framework writes{_MEANING.get(name, '')}. Rename it"
                f"{_RENAMED.get(name, '')}."
            )

    @classmethod
    def translations(cls) -> Translated:
        """The aggregate's own name, in every language the project speaks. Override it.

            {"default": "Note", "es": "Nota"}

        Read by the definition of the model, once per class. A field's own label and help text
        are not here — see `field(metadata={"label": …, "help": …})`.
        """
        return {"default": cls.__name__}

    @classmethod
    def DEFAULT_GET_ID(cls) -> str:
        """The field `Get` and `GetMany` find a record by. Override it for a natural key.

            return "code"           QueryGetAccount(id="1.2.3") reads the account coded 1.2.3

        Only how a record is asked for: the identity it is stored, related and ordered by is
        still `id`. Checked when the class is described: a scalar field of its own.
        """
        return "id"

    @classmethod
    def DEFAULT_READING(cls) -> Specification | None:
        """What one record brings when the caller names nothing: `Get`, `GetMany`, `Search`.

            return Specification.model_validate(
                {"title": {}, "runs": {"specification": {"steps": {}}}}
            )

        A `Specification`, not a `Criteria`: it says what of each record, at any depth, and
        never which records — a filter here would hide records from every read. `None` is
        every scalar and no relation. A caller's `criteria.specification` replaces it.
        """
        return None

    @classmethod
    def DEFAULT_ORDER(cls) -> tuple[Sort, ...]:
        """The order of a list, and of this entity's records where another one holds them,
        when the caller names none. Newest identity first unless overridden.

            return (Sort(field="code"),)
        """
        return (Sort(field="id", descending=True),)

    @classmethod
    def DEFAULT_DISPLAY(cls) -> str:
        """The field shown beside the identity: what a select lists and a reference carries.
        The field called `name` when there is one; empty when nothing is shown."""
        return "name" if "name" in {one.name for one in dataclasses.fields(cls)} else ""

    @classmethod
    def DEFAULT_LITERAL_SEARCH(cls) -> Criteria | None:
        """How a text a person typed finds records: a template whose `where` holds `TEXT`.

            return Criteria(where=Any([
                Condition(field="code", operator=Operator.STARTS_WITH, value=TEXT),
                Condition(field="name", operator=Operator.LIKE, value=TEXT),
            ]))

        A template rather than a function of the text, so `Meta` publishes it and a client
        fills it the same way. Containment on a text display unless overridden; `None` when
        there is nothing to search.
        """
        # Imported here: entity_meta reads this module to describe a class.
        from sincpro_framework.ddd.entity.entity_meta import (
            TEXT_TYPES,
            annotations_of,
            logical_type,
        )

        display = cls.DEFAULT_DISPLAY()
        annotation = annotations_of(cls).get(display)
        if annotation is None or logical_type(annotation) not in TEXT_TYPES:
            return None
        return Criteria(where=Condition(field=display, operator=Operator.LIKE, value=TEXT))

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
