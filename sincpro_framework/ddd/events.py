"""What happened, said once by the aggregate; who hears it is somebody else's business.

    class DatasetRegistered(DomainEvent):
        dataset_id: str
        name: str

    dataset.record(DatasetRegistered(dataset_id=dataset.id, name=dataset.name))
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
"""

from datetime import datetime

from pydantic import Field

from sincpro_framework.ddd.entity import new_entity_id, utc_now
from sincpro_framework.sincpro_abstractions import DataTransferObject


class DomainEvent(DataTransferObject):
    """The envelope every event carries; a subclass adds what happened.

    `event_id` is a UUID v7, so ordering by id is ordering by time. `correlation_id` names
    the original request behind all of this and `causation_id` the event that directly caused
    this one — the two lines a person follows through the logs of three services.
    """

    event_id: str = Field(default_factory=new_entity_id)
    occurred_at: datetime = Field(default_factory=utc_now)
    aggregate_type: str = ""
    aggregate_id: str = ""
    correlation_id: str | None = None
    causation_id: str | None = None
    sequence: int = 0
    """The position among the events one aggregate recorded in one transaction, so a
    consumer can replay them in the order they were said."""

    @property
    def name(self) -> str:
        """What the event is called on the wire: its class name, which is also the key the
        bus routes it by.

        >>> DatasetRegistered(...).name
        'DatasetRegistered'
        """
        return type(self).__name__
