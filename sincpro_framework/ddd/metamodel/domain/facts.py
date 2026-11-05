"""What a store knows about an entity that the entity does not say: what it can answer as data,
never a description of its own.

    StoreFacts(identity="id")                                     memory: the annotations say it all
    StoreFacts(identity="id", stored={"total": StoredField(…)},
               relations={"lines": FieldMeta.for_relation(…)})    SQL: columns and how relations meet
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sincpro_framework.ddd.entity.entity_meta import FieldMeta, Meta


@dataclass(frozen=True)
class StoredField:
    """A field as the store keeps it: what it holds, and whether a row may leave it empty."""

    annotation: Any
    nullable: bool


@dataclass(frozen=True)
class StoreFacts:
    """What a store adds to a model's description.

    `identity` is the field a record is found by. `stored` are the store's own fields in its order,
    each with what it holds and whether it may be empty — `None` when the annotations say it.
    `relations` are how the store keeps each relation; `extended`, the fields an extension it holds
    adds, each marked with its owner; `related` describes, by a relation's name, what it points at
    — what a filter crossing it (`Through`) is checked against.
    """

    identity: str
    stored: Mapping[str, StoredField] | None = None
    relations: Mapping[str, FieldMeta] = field(default_factory=dict)
    extended: Mapping[str, FieldMeta] = field(default_factory=dict)
    related: Callable[[str], Meta | None] | None = None
