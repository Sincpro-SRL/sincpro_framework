"""What happened, said once by the aggregate; who hears it is somebody else's business.

    class DatasetRegistered(DomainEvent):
        dataset_id: str
        source: str

    dataset.record(DatasetRegistered(dataset_id=dataset.id, source=dataset.source))
    ...
    for event in dataset.pull_events():   # explicit: the Feature decides what to do with them
        self.publisher.publish(event)

**An event is a DTO, and that is the whole design.** The framework already routes a DTO to
the Feature registered for its class name, so a subscriber is nothing new: a Feature on some
bounded context's bus, registered for the event class — `@bus.feature([CommandX, EventY])`
takes both. There is no second registry to keep in step with the first.

The aggregate records; it never publishes. Publishing from `domain/` would put a bus there and
announce facts a rollback then undoes. What is recorded lives in memory and dies with the
object: nothing here stores an event. What carries them is `sincpro_framework.events`.

Names are past tense because an event is a fact that already happened; the imperative belongs
to commands.

**`name` is what a class is called on the wire — everywhere the framework routes, stores or
matches an event — and it defaults to the class's own Python name but is not tied to it.**
Every subclass gets its own `name` stamped the moment it is declared, so renaming the Python
class later never changes what was already routed or stored under the old one. Set it
explicitly, before the class ever ships, the same way `sincpro_mobile`'s event queue already
does (`common.queue_event.v2.start`) — `context.aggregate.vN.event`, so two contexts never
collide on one name and a breaking shape change gets its own revision instead of silently
reinterpreting old rows:

    class RunAdvanced(DomainEvent):
        name = "execution.run.v1.advanced"
        dataset_id: str
        stage: str

A further subclass that sets nothing gets its own default (its own class name), never the
parent's override — `RunAdvancedDebug(RunAdvanced)` is not secretly `"execution.run.v1.advanced"`
unless it says so itself.

**`name` is reserved.** A subclass overrides it as a plain assignment (`name = "..."`), never
a typed field (`name: str`) — a typed one would become a real, silently-defaulted dataclass
field instead of the wire name, which is refused at class-declaration time, loudly.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from sincpro_framework.ddd.entity import Entity, new_entity_id, utc_now
from sincpro_framework.ddd.exceptions import ContractViolation

NAME = "name"


@dataclass(kw_only=True)
class DomainEvent(Entity):
    """The envelope every event carries; a subclass adds what happened.

    `id` is a UUID v7, so ordering by id is ordering by time. `correlation_id` names
    the original request behind all of this and `causation_id` the event that directly caused
    this one — the two lines a person follows through the logs of three services.
    """

    name: ClassVar[str] = ""
    """Set on every subclass by `__init_subclass__` below — declared here only so a type
    checker knows every `DomainEvent` has one."""

    id: str = field(default_factory=new_entity_id)
    label: dict[str, str] = field(default_factory=dict)
    """A human text for this one occurrence — `{"default": "Run advanced", "es": "Ejecución
    avanzada"}`"""
    created_at: datetime = field(default_factory=utc_now)
    entity_type: str = ""
    entity_id: str = ""
    correlation_id: str | None = None
    causation_id: str | None = None
    sequence: int = 0
    """The position among the events one aggregate recorded in one transaction, so a
    consumer can replay them in the order they were said."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if NAME in cls.__dict__.get("__annotations__", {}):
            raise ContractViolation(
                f"{cls.__name__} declares 'name' as a typed field — 'name' is reserved for "
                "the wire name every DomainEvent subclass gets (see this module's docstring). "
                "Rename the field, or drop the annotation to override the wire name instead: "
                f'name = "{cls.__name__}"'
            )
        if NAME not in cls.__dict__:
            cls.name = cls.__name__

    def __repr__(self) -> str:
        return f"{self.name}(id={self.id}, label={self.label}, entity_type={self.entity_type}, entity_id={self.entity_id}, created_at={self.created_at.isoformat()})"
