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

import sqlalchemy
from sqlalchemy import Table, and_
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.orm import registry

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.events.domain_event import NAME
from sincpro_framework.ddd.events.mixins.deliverable import (
    DELIVERY_FIELDS,
    DeliverableEventMixin,
)
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.sincpro_logger import logger

ENVELOPE = frozenset(one.name for one in dataclasses.fields(DomainEvent))
"""What every event carries; what a subclass declares on top is its payload."""

COLUMNS_OF_DELIVERY = DELIVERY_FIELDS
"""A deliverable event's delivery fields are columns of their own, never its payload."""

bases: dict[type, tuple[registry, Table]] = {}
"""Each mapped base event class, with the registry and table its subclasses go to."""

_named: dict[Table, dict[str, type]] = {}
"""Per event table, which class each stored `name` is read as — the name is the event's identity,
so one name is one class."""

UNKNOWN_EVENT = "unknown_event"
"""How a read reports rows it left out because no class of this process is named like them."""


def _is_mapped(cls: type) -> bool:
    try:
        sqlalchemy.inspect(cls)
    except NoInspectionAvailable:
        return False
    return True


def packed(_mapper: Any, _connection: Any, target: Any) -> None:
    """Before an event is inserted: what its class adds goes to `payload`, and a deliverable one
    is marked as such. Its `name` is a column of its own, never part of the payload."""
    written = json.loads(target.as_json())
    target.payload_of_class = {
        key: value
        for key, value in written.items()
        if key not in ENVELOPE and key not in COLUMNS_OF_DELIVERY and key != NAME
    }
    target.is_deliverable = isinstance(target, DeliverableEventMixin)


def unpacked(target: Any, _context: Any) -> None:
    """After an event is loaded: the fields its class adds, from `payload`, typed as the class
    declares them — the mapper builds the event without calling `__init__`."""
    known = target.__dict__
    whole = {name: known[name] for name in ENVELOPE if name in known}
    whole.update(known.get("payload_of_class") or {})
    rebuilt = cast(Any, type(target)).from_json(whole)
    for one in dataclasses.fields(type(target)):
        if one.name not in known:
            known[one.name] = getattr(rebuilt, one.name)


def mapped_before_built(cls: type, *_args: Any, **_kwargs: Any) -> Any:
    """An event class declared after its base was mapped is mapped before its first instance
    is built: SQLAlchemy cannot build an instance of a class it has not mapped."""
    if not _is_mapped(cls):
        map_new_event_classes()
    return object.__new__(cls)


def claim(table: Table, cls: type) -> None:
    """`cls` is what `cls.name` reads as in `table` — refused when another class holds the name.

        in      Posted (name "Posted") then Reposted (name "Posted"), one table
        out     ContractViolation naming both

    Two classes under one name would make the second the reading of every row the first wrote:
    its fields lost, the wrong class answered, and nothing said.
    """
    held = _named.setdefault(table, {})
    other = held.get(cls.name)  # type: ignore[attr-defined]
    if other is not None and other is not cls:
        raise ContractViolation(
            f"{other.__name__} and {cls.__name__} are both named '{cls.name}' in the event "  # type: ignore[attr-defined]
            f"table {table.name}: a name is the event's identity, so each class needs its own "
            f'— name = "<context>.<aggregate>.<event>"'
        )
    held[cls.name] = cls  # type: ignore[attr-defined]


def readable_names(model: type) -> list[str] | None:
    """The names a read of `model` can turn into events, when `model` is a mapped base whose
    table may hold rows no class of this process is named like; `None` for anything else.

        in      LedgerEvent (mapped base)      →  out  ["Posted", "t.ledger.v1.event", …]
        in      Posted, Account                →  out  None

    A subclass needs nothing: its read is already narrowed to its own names.
    """
    if model not in bases:
        return None
    map_new_event_classes()
    return sorted(_named.get(bases[model][1], {}))


def map_new_event_classes() -> None:
    """Maps every subclass of a mapped base that is not mapped yet, parents before children."""
    for base, (mapper_registry, table) in list(bases.items()):
        pending = list(base.__subclasses__())
        while pending:
            cls = pending.pop(0)
            if not _is_mapped(cls):
                claim(table, cls)
                parent = next(one for one in cls.__mro__[1:] if _is_mapped(one))
                mapper_registry.map_imperatively(
                    cls, inherits=parent, polymorphic_identity=cls.name
                )
            pending.extend(cls.__subclasses__())


def is_mapped_event(cls: type) -> bool:
    """Whether events of `cls` have a table: it is, or descends from, a mapped base."""
    return any(issubclass(cls, base) for base in bases)


UNREADABLE_SHOWN = 20
"""How many unknown names one read looks up to log; the report itself is one `Dropped`."""

_warned: set[tuple[str, str]] = set()
"""(event base, name) pairs already logged, so a periodic read does not repeat itself."""


def readable_clause(model: type, clause: Any) -> Any:
    """`clause`, narrowed to the names this process can read when `model` is a mapped event base.

    in      LedgerEvent, entity_id = 'a1'
    out     entity_id = 'a1' AND name IN ('Posted', 't.ledger.v1.event', …)
    """
    readable = readable_names(model)
    if readable is None:
        return clause
    known = cast(Any, model).wire_name.in_(readable)
    return known if clause is None else and_(clause, known)


def warn_unreadable(model: type, names: list[str]) -> None:
    """Each unknown name once per process: which event base holds rows nobody can read."""
    for name in names:
        if (model.__name__, name) in _warned:
            continue
        _warned.add((model.__name__, name))
        logger.warning(
            f"{model.__name__} holds events named '{name}' and no class of this process is "
            "named like them: they are left out of every read until that class is imported"
        )
