"""One `Updated` event with everything that changed, instead of one per field a Feature set.

    @dataclass
    class Note(ChangeTrackingMixin, Entity):
        title: str
        body: str = ""
        render_cache: str = dataclasses.field(default="", metadata={"tracked": False})

    note = repository.get(Note, note_id)
    note.title = "New title"
    note.body = "New body"
    repository.save(note)
    note.pull_events()   # [EntityUpdated(changes={"title": (…, …), "body": (…, …)})]

An entity-side mixin, opt-in like `AuditedMixin`/`ArchivableMixin` beside it. **This half says
what counts as a change and what the event is called; it does not say when to look.** That is
the store's, and the two stores do not do it alike: SQLAlchemy asks the engine what it is about
to write (`orm/sqlalchemy/change_tracking.py`), and a store with no flush compares against a
baseline it took on the way out (`ddd/repositories/change_tracking.py`). Either way the diff
arrives at `record_change`, and the aggregate turns it into its own event.

**Snapshot, then diff, never per-assignment** — for the baseline half. A field is compared
against the value it held right after the aggregate was last read (or, for one never persisted,
right after it was built) against the value it holds when it is about to be saved. Setting a
field twice and landing back
on the original value nets to no change — the point of comparing snapshots rather than watching
every `=`.

**Nothing here depends on a database, a queue or an outbox.** `record()` is still the same
in-memory list it always was (`entity.py`); if nobody calls `pull_events()`, nothing happens
beyond this call.
"""

import dataclasses
from typing import Any, ClassVar, cast

from sincpro_framework.ddd.entity.entity import Entity
from sincpro_framework.ddd.entity.mixins.archivable import ArchivableMixin
from sincpro_framework.ddd.entity.mixins.audited import AuditedMixin
from sincpro_framework.ddd.entity.model_meta import (
    annotations_of,
    field_translations,
    related_class,
)
from sincpro_framework.ddd.events import DomainEvent

SNAPSHOT = "_change_snapshot"
"""Where a record keeps the field values it is compared against, by name. Lives in `__dict__`,
like `RECORDED` in `entity.py` — bookkeeping, never a dataclass field."""

_RESERVED = frozenset(
    field.name
    for base in (Entity, AuditedMixin, ArchivableMixin)
    for field in dataclasses.fields(base)
)
"""Every field `Entity` and its framework mixins already declare. Computed off the mixins
themselves, not typed by hand, so a new one added later is reserved automatically."""


def _is_another_aggregate(annotation: Any) -> bool:
    """Whether this annotation points at an `Entity` — `Author | None`, `list[Run]`. Read off
    the annotation and nothing else: the domain layer knows an aggregate from a value object by
    what it inherits, never by whether an ORM happens to map it."""
    if annotation is None:
        return False
    related, _many = related_class(annotation)
    return isinstance(related, type) and issubclass(related, Entity)


@dataclasses.dataclass(kw_only=True)
class EntityUpdated(DomainEvent):
    """What any `ChangeTrackingMixin` aggregate emits by default — a dev overrides
    `change_event` only when a domain-specific name earns its place."""

    name = "ddd.entity.v1.updated"

    label: dict[str, str] = dataclasses.field(
        default_factory=lambda: {"default": "Updated", "es": "Se actualizó"}
    )
    """What this event is, in the languages the framework ships. `entity_type` already says
    *what* was updated, so the sentence does not repeat it.

    **Spanish says "Se actualizó" and not "Actualizado" on purpose**: an adjective would have
    to agree with a noun the event does not know the gender of. A subclass that means something
    more precise sets its own default the same way.
    """

    changes: dict[str, tuple[Any, Any]]
    """`{field: (before, after)}`, one entry per field that actually differs."""

    field_labels: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)
    """How a screen names each field that changed, as it was named *then* —
    `{"total": {"default": "Total", "es": "Importe"}}`, read off the aggregate's
    `field(metadata={"label": …})` when the event was recorded.

    **Carried rather than resolved when somebody reads it**, for two reasons. An audit says
    what a person saw at the time: rename a label next year and the old entries have to keep
    the old word, or the record has been rewritten. And an event that crossed to another
    service has no model class there to ask.

    Empty for a field that declared no label — there is nothing to say about it.
    """


class ChangeTrackingMixin:
    """Opt-in, no fields of its own — a subclass declares nothing beyond what it already has.

    @dataclass
    class Note(ChangeTrackingMixin, Entity):
        title: str
        secret: str = field(metadata={"tracked": False})   # excluded, everything else isn't

        change_event: ClassVar[type[DomainEvent]] = NoteUpdated   # optional, defaults below
    """

    change_event: ClassVar[type[DomainEvent]] = EntityUpdated

    def __post_init__(self) -> None:
        """A freshly built aggregate starts compared against its own constructor values.

        Calls further along the chain first: mixed beside another mixin that has one of these,
        stopping here would swallow it silently — the aggregate would build with that mixin's
        work simply not done, and nothing would say so.
        """
        following = getattr(super(), "__post_init__", None)
        if following is not None:
            following()
        self._snapshot()

    @classmethod
    def tracked_fields(cls) -> frozenset[str]:
        """Every field this aggregate watches, by name. Everything it declares is tracked
        except three kinds:

            id, created_at, version, created_by, archived_at …   what `Entity` and its
                                                                 mixins already own
            field(metadata={"tracked": False})                   what the field itself excludes
            runs: list[Run] · author: Author | None              another aggregate

        **A field pointing at another `Entity` is never tracked**, single or several. It is not
        a value of this aggregate, it is a different one: a change there is that aggregate's own
        `Updated` event, not this one's. Tracking it would also put whole records inside the
        event — every column of every related row, growing with the relation.

        A value object (a plain dataclass, not an `Entity`) *is* part of this aggregate and is
        tracked: `size: Shape` travels in the event as the shape it is.
        """
        annotations = annotations_of(cls)
        return frozenset(
            field.name
            for field in dataclasses.fields(cast(Any, cls))
            if field.name not in _RESERVED
            and field.metadata.get("tracked", True)
            and not _is_another_aggregate(annotations.get(field.name))
        )

    def _said_about(self, changed: dict[str, tuple[Any, Any]]) -> dict[str, Any]:
        """What the event is built from: the diff, plus the label of each field that moved —
        only those, never the whole model. A `change_event` without `field_labels` gets the
        diff alone."""
        said: dict[str, Any] = {"changes": changed}
        fields = dataclasses.fields(cast(Any, self.change_event))
        known = {one.name for one in fields}
        if "field_labels" in known:
            labels = field_translations(type(self), "label")
            said["field_labels"] = {name: labels[name] for name in changed if name in labels}
        return said

    def record_change(self, changed: dict[str, tuple[Any, Any]]) -> "DomainEvent | None":
        """One consolidated `change_event` for a diff **the store worked out**, recorded on
        this aggregate and handed back. `None` when nothing differs.

        The aggregate knows what its event is called and what words go on it; it does not know
        what actually reached the database. A store that can answer that exactly — SQLAlchemy
        keeps the before and after of every column it is about to write — says so here instead
        of the aggregate keeping a baseline and guessing.
        """
        if not changed:
            return None
        return self.record(self.change_event(**self._said_about(changed)))  # type: ignore[attr-defined]

    def _snapshot(self, *, force: bool = False) -> None:
        """Sets the baseline `changes()` compares against. `force=False` (a plain read) never
        overwrites one already there — the same object handed back twice inside one unit of
        work must not lose the values it was first read with."""
        if force or SNAPSHOT not in self.__dict__:
            self.__dict__[SNAPSHOT] = {
                name: getattr(self, name) for name in self.tracked_fields()
            }

    def changes(self) -> dict[str, tuple[Any, Any]]:
        """`{field: (before, after)}` for every tracked field that actually differs from the
        snapshot — empty when nothing does, including when a field went out and came back."""
        before = self.__dict__.get(SNAPSHOT, {})
        return {
            name: (old, getattr(self, name))
            for name, old in before.items()
            if old != getattr(self, name)
        }

    def record_changes(self) -> "DomainEvent | None":
        """Consolidates whatever changed into one `change_event`, records it, and hands it
        back — so an explicit caller can publish it or store it right there:

            event = invoice.record_changes()
            if event is not None:
                events.save(event)                 # an event store: `save` like any aggregate
                publisher.publish(event)           # or a queue: somebody reacts to it

        `None` when nothing differs, or when this is the first save ever — that is a Created
        fact, written by hand, not this.

        **What comes back is the same event `pull_events()` will hand over**, not a second
        one: publish one or the other, never both. Whatever it answers, the baseline moves to
        the current state, so the next cycle compares from here.

        This is the baseline half, which a store with no flush calls on every `save()`. A store
        that can ask its engine what changed calls `record_change` with that answer instead;
        calling either by hand is the explicit mode, and all of them end in the same place.
        """
        if self.is_new:  # type: ignore[attr-defined]
            return None
        recorded = self.record_change(self.changes())
        self._snapshot(force=True)
        return recorded
