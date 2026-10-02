"""Domain events mapped as entities: a bounded context's base event class to its one table, and
every subclass of it to the same table — SQLAlchemy's single-table inheritance, the wire name as
the discriminator.

    events = event_table("billing_events", metadata)
    map_events(mapper, BillingDomainEvent, events)

    repository.save([DayClosed(), InvoicePaid(invoice_id="F-1", amount=10)])
    repository.search(InvoicePaid, criteria)       → [InvoicePaid, …]; only that class's rows
    repository.search(BillingDomainEvent, criteria) → every event of the context, each as its class

**The envelope is columns, what a class adds is `payload`.** `id`, `entity_type`, `entity_id`,
`entity_version`, `correlation_id`, `causation_id`, `label` and `created_at` are filtered like
any column; the fields a subclass declares are written into `payload` on insert and set back on
the event when it is loaded. A deliverable event's `delivered_at`, `next_delivery_at` and `delivery` are
columns too, beside the fact, and never sent.

**Identified and ordered by its id** — a UUID v7, so ordering by it is ordering by time —
`get(InvoicePaid, event_id)` works like any entity.

**A subclass is mapped the first time it is used** — declared after `map_events`, imported late
— so a project maps its base once and never lists its events.
"""

import dataclasses
import json
from typing import Any, cast

from sqlalchemy import Table, event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.orm import registry

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.events.mixins import DELIVERY_FIELDS, DeliverableEventMixin
from sincpro_framework.ddd.exceptions import ContractViolation

ENVELOPE = frozenset(one.name for one in dataclasses.fields(DomainEvent))
"""What every event carries; what a subclass declares on top is its payload."""

COLUMNS_OF_DELIVERY = DELIVERY_FIELDS
"""A deliverable event's delivery fields are columns of their own, never its payload."""

_bases: dict[type, tuple[registry, Table]] = {}
"""Each mapped base event class, with the registry and table its subclasses go to."""


def _is_mapped(cls: type) -> bool:
    try:
        sa_inspect(cls)
    except NoInspectionAvailable:
        return False
    return True


def _packed(_mapper: Any, _connection: Any, target: Any) -> None:
    """Before an event is inserted: what its class adds goes to `payload`, and a deliverable one
    is marked as such."""
    written = json.loads(target.as_json())
    target.payload_of_class = {
        key: value
        for key, value in written.items()
        if key not in ENVELOPE and key not in COLUMNS_OF_DELIVERY
    }
    target.is_deliverable = isinstance(target, DeliverableEventMixin)


def _unpacked(target: Any, _context: Any) -> None:
    """After an event is loaded: the fields its class adds, from `payload`, typed as the class
    declares them — the mapper builds the event without calling `__init__`."""
    known = target.__dict__
    whole = {name: known[name] for name in ENVELOPE if name in known}
    whole.update(known.get("payload_of_class") or {})
    rebuilt = cast(Any, type(target)).from_json(whole)
    for one in dataclasses.fields(type(target)):
        if one.name not in known:
            known[one.name] = getattr(rebuilt, one.name)


def _mapped_before_built(cls: type, *_args: Any, **_kwargs: Any) -> Any:
    """An event class declared after its base was mapped is mapped before its first instance
    is built: SQLAlchemy cannot build an instance of a class it has not mapped."""
    if not _is_mapped(cls):
        map_new_event_classes()
    return object.__new__(cls)


def map_events(mapper_registry: registry, base: type[DomainEvent], table: Table) -> None:
    """Maps `base` — the bounded context's event class — and every subclass of it to `table`.

    A base per context, so two contexts in one process keep two tables; mapping it again is a
    no-op.
    """
    if not (isinstance(base, type) and issubclass(base, DomainEvent)):
        raise ContractViolation(
            f"{base!r} is not a DomainEvent class: there is nothing to map"
        )
    if base in _bases:
        return
    mapper_registry.map_imperatively(
        base,
        table,
        polymorphic_on=table.c.name,
        polymorphic_identity=base.name,
        properties={
            "wire_name": table.c.name,
            "payload_of_class": table.c.payload,
        },
    )
    event.listen(base, "before_insert", _packed, propagate=True)
    event.listen(base, "load", _unpacked, propagate=True)
    setattr(base, "__new__", staticmethod(_mapped_before_built))
    _bases[base] = (mapper_registry, table)
    map_new_event_classes()


def map_new_event_classes() -> None:
    """Maps every subclass of a mapped base that is not mapped yet, parents before children."""
    for base, (mapper_registry, _table) in list(_bases.items()):
        pending = list(base.__subclasses__())
        while pending:
            cls = pending.pop(0)
            if not _is_mapped(cls):
                parent = next(one for one in cls.__mro__[1:] if _is_mapped(one))
                mapper_registry.map_imperatively(
                    cls, inherits=parent, polymorphic_identity=cls.name
                )
            pending.extend(cls.__subclasses__())


def is_mapped_event(cls: type) -> bool:
    """Whether events of `cls` have a table: it is, or descends from, a mapped base."""
    return any(issubclass(cls, base) for base in _bases)
