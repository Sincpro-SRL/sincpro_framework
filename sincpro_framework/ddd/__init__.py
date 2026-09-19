"""Domain vocabulary every Sincpro service shares, with no infrastructure behind it.

`ValueObject` builds a validated primitive. `Entity` is what an aggregate carries and how it
names itself for a screen. `Criteria` says what to read, `EntityCollection` what came back, `Meta`
what a model publishes about itself, `Repository` the least a store answers, and
`DomainEvent` what happened.

Nothing here imports an ORM. Running a criteria against a database is `sincpro_framework.orm`,
an optional extra; carrying events is `sincpro_framework.events`.
"""

from sincpro_framework.ddd.criteria import (
    Bucket,
    Condition,
    CountMode,
    Criteria,
    Cursor,
    Offset,
    Operator,
    Pagination,
    Pivot,
    PivotCell,
    Sort,
    Specification,
    matches,
)
from sincpro_framework.ddd.entity import (
    ArchivableMixin,
    AuditedMixin,
    ChangeTrackingMixin,
    Entity,
    EntityUpdated,
    Translated,
    new_entity_id,
    utc_now,
)
from sincpro_framework.ddd.entity.entity_collection import (
    Changes,
    Count,
    Dropped,
    EntityCollection,
)
from sincpro_framework.ddd.entity.model_meta import FieldMeta, FieldType, Meta
from sincpro_framework.ddd.entity.relations import BusResolver, Relation, Resolver
from sincpro_framework.ddd.events import DomainEvent, EventStatus, EventTrackableMixin
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    DomainError,
    DuplicateAggregate,
    InvalidCriteria,
    RelationNotResolved,
    StaleAggregate,
)
from sincpro_framework.ddd.query import Query, ResponsePaginatedQuery
from sincpro_framework.ddd.repositories import (
    ChangeTrackingRepositoryMixin,
    MemoryRepository,
    Repository,
)
from sincpro_framework.ddd.repositories.hooks import Hook, Hooks, Rule
from sincpro_framework.ddd.value_object import ValueObject

__all__ = [
    "ArchivableMixin",
    "AuditedMixin",
    "Bucket",
    "BusResolver",
    "EntityCollection",
    "ChangeTrackingMixin",
    "Condition",
    "Changes",
    "Count",
    "CountMode",
    "Criteria",
    "Cursor",
    "DomainEvent",
    "Dropped",
    "DuplicateAggregate",
    "Entity",
    "EntityUpdated",
    "ChangeTrackingRepositoryMixin",
    "Hook",
    "Hooks",
    "EventStatus",
    "FieldMeta",
    "FieldType",
    "InvalidCriteria",
    "MemoryRepository",
    "Meta",
    "Offset",
    "Operator",
    "DomainError",
    "ContractViolation",
    "Pagination",
    "Pivot",
    "PivotCell",
    "Query",
    "Relation",
    "Repository",
    "Rule",
    "Resolver",
    "ResponsePaginatedQuery",
    "Sort",
    "Specification",
    "RelationNotResolved",
    "StaleAggregate",
    "EventTrackableMixin",
    "Translated",
    "ValueObject",
    "matches",
    "new_entity_id",
    "utc_now",
]
